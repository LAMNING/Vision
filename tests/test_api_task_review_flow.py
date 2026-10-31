"""任务流的复核契约：逐图复核 → 任务自动收口，`review_required=False` 直接通过。

覆盖的优化项：
- 任务列表里的 `复核 x / y 张` 计数（`TaskListItem.review_approved` / `review_rejected`）；
- 逐图全部通过后任务自动 `approved`（`review.finalize_approved`），不再挂在待复核；
- 建任务时取消「需要审核」（`Task.review_required=False`）→ 标完即通过；
- 任务级退回仍然要有人工裁决，且必须至少一张图被驳回。
"""

import pytest

from conftest import (
    annotate_image,
    complete_task,
    create_task,
    make_category,
    make_project,
    project_tasks,
    review_task_image,
    submit_task,
    task_detail,
    upload_images,
)


@pytest.fixture()
def scene(client, user):
    project = make_project(client, user, '复核流程')
    make_category(client, user, project['id'])
    image_ids = upload_images(client, user, project['id'], count=2)
    return {'project': project, 'images': image_ids}


def prepare_manual_task(client, user, scene, *, review_required=True, name='待复核'):
    """建人工任务 → 提交 → 每张图各标一个框 → 完成，落到 auto_annotated。"""
    project_id = scene['project']['id']
    task = create_task(
        client,
        user,
        project_id,
        scene['images'],
        mode='manual',
        review_required=review_required,
        name=name,
    )
    submit_task(client, user, project_id, task['id'])
    detail = task_detail(client, user, project_id, task['id'])
    for index, row in enumerate(detail['images']):
        annotate_image(
            client,
            user,
            project_id,
            task['id'],
            row['id'],
            [(10 + index, 10, 120, 90)],
        )
    complete_task(client, user, project_id, task['id'])
    return task['id']


def test_task_list_exposes_review_counters(client, user, scene):
    task_id = prepare_manual_task(client, user, scene)
    rows = project_tasks(client, user, scene['project']['id'])
    row = next(item for item in rows if item['id'] == task_id)
    assert row['status'] == 'auto_annotated'
    assert row['image_total'] == 2
    assert row['image_annotated'] == 2
    assert row['review_approved'] == 0
    assert row['review_rejected'] == 0
    assert row['review_required'] is True


def test_approving_every_image_finalizes_task(client, user, scene):
    project_id = scene['project']['id']
    task_id = prepare_manual_task(client, user, scene)
    rows = task_detail(client, user, project_id, task_id)['images']

    first = review_task_image(client, user, project_id, task_id, rows[0]['id'], approved=True)
    assert first.status_code == 200, first.text
    assert task_detail(client, user, project_id, task_id)['status'] == 'auto_annotated'
    listed = {row['id']: row for row in project_tasks(client, user, project_id)}[task_id]
    assert listed['review_approved'] == 1

    second = review_task_image(client, user, project_id, task_id, rows[1]['id'], approved=True)
    assert second.status_code == 200, second.text
    detail = task_detail(client, user, project_id, task_id)
    assert detail['status'] == 'approved'
    assert all(row['review_status'] == 'approved' for row in detail['images'])

    reviews = client.get(f'/projects/{project_id}/tasks/{task_id}/reviews', headers=user.headers)
    assert reviews.status_code == 200, reviews.text
    payload = reviews.json()
    assert len(payload) == 1
    assert payload[0]['decision'] == 'approve'
    assert payload[0]['comment'] == '逐图复核全部通过'
    assert payload[0]['approved_count'] == 2


def test_mixed_verdict_keeps_task_in_review(client, user, scene):
    project_id = scene['project']['id']
    task_id = prepare_manual_task(client, user, scene)
    rows = task_detail(client, user, project_id, task_id)['images']

    ok = review_task_image(client, user, project_id, task_id, rows[0]['id'], approved=True)
    bad = review_task_image(
        client, user, project_id, task_id, rows[1]['id'], approved=False, reason='框偏了'
    )
    assert ok.status_code == 200 and bad.status_code == 200, (ok.text, bad.text)

    detail = task_detail(client, user, project_id, task_id)
    assert detail['status'] == 'auto_annotated'
    listed = {row['id']: row for row in project_tasks(client, user, project_id)}[task_id]
    assert listed['review_approved'] == 1
    assert listed['review_rejected'] == 1

    # 有一张被驳回时，任务级退回仍然可用
    decision = client.post(
        f'/projects/{project_id}/tasks/{task_id}/review',
        json={'decision': 'reject_fix', 'comment': '按驳回原因改一版'},
        headers=user.headers,
    )
    assert decision.status_code == 200, decision.text
    assert task_detail(client, user, project_id, task_id)['status'] == 'rejected_fix'


def test_reject_needs_reason(client, user, scene):
    project_id = scene['project']['id']
    task_id = prepare_manual_task(client, user, scene)
    row = task_detail(client, user, project_id, task_id)['images'][0]

    response = review_task_image(client, user, project_id, task_id, row['id'], approved=False)
    assert response.status_code == 422
    assert 'reject 必须提供 reject_reason' in response.text


def test_task_approve_requires_every_image_decided(client, user, scene):
    project_id = scene['project']['id']
    task_id = prepare_manual_task(client, user, scene)
    row = task_detail(client, user, project_id, task_id)['images'][0]
    review_task_image(client, user, project_id, task_id, row['id'], approved=True)

    response = client.post(
        f'/projects/{project_id}/tasks/{task_id}/review',
        json={'decision': 'approve'},
        headers=user.headers,
    )
    assert response.status_code == 400
    assert 'cannot approve' in response.text


def test_review_not_required_approves_on_completion(client, user, scene):
    project_id = scene['project']['id']
    task = create_task(
        client,
        user,
        project_id,
        scene['images'][:1],
        mode='manual',
        review_required=False,
        name='免复核',
    )
    submit_task(client, user, project_id, task['id'])
    detail = task_detail(client, user, project_id, task['id'])
    annotate_image(client, user, project_id, task['id'], detail['images'][0]['id'], [(20, 20, 90, 80)])

    completed = complete_task(client, user, project_id, task['id'])
    assert completed['review_required'] is False
    assert completed['status'] == 'approved'

    images = client.get(
        f'/projects/{project_id}/images', params={'stage': 'approved'}, headers=user.headers
    ).json()
    assert images['total'] == 1
    assert images['items'][0]['review_status'] == 'approved'


def test_default_review_required_is_true(client, user, scene):
    project_id = scene['project']['id']
    response = client.post(
        f'/projects/{project_id}/tasks',
        json={'project_id': project_id, 'mode': 'manual', 'image_ids': scene['images'][:1]},
        headers=user.headers,
    )
    assert response.status_code in (200, 201), response.text
    assert response.json()['review_required'] is True
