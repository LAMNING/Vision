"""图像 tab 的「这张图标注过 / 审核过没有」——`ProjectImageRead` 的聚合与筛选。

覆盖的优化项：图像 tab 纯文件表的状态标签、行内「审核 / 标注」入口。
卡片规则：stage 取这张图所有任务里优先级最高的一条
（可编辑 > 已通过 > 已退回 > 可审核 > 已标注 > 失败 > 待标注）。
"""

import pytest

from conftest import (
    annotate_image,
    complete_task,
    create_task,
    make_category,
    make_project,
    project_images,
    review_task_image,
    submit_task,
    task_detail,
    upload_images,
)


@pytest.fixture()
def scene(client, user):
    project = make_project(client, user, '图像进度')
    make_category(client, user, project['id'])
    image_ids = upload_images(client, user, project['id'], count=2)
    return {'project': project, 'images': image_ids}


def test_new_image_stage_is_none(client, user, scene):
    page = project_images(client, user, scene['project']['id'])
    assert page['total'] == 2
    for item in page['items']:
        assert item['stage'] == 'none'
        assert item['annotated'] is False
        assert item['review_status'] == 'none'
        assert item['task_count'] == 0
        assert item['can_annotate'] is False
        assert item['can_review'] is False


def test_pending_gives_annotate_entry(client, user, scene):
    project_id = scene['project']['id']
    task = create_task(client, user, project_id, scene['images'][:1])
    submit_task(client, user, project_id, task['id'])

    page = project_images(client, user, project_id, stage='pending')
    assert page['total'] == 1
    item = page['items'][0]
    assert item['stage'] == 'pending'
    assert item['can_annotate'] is True
    assert item['can_review'] is False
    assert item['task_id'] == task['id']
    assert item['task_mode'] == 'manual'
    assert item['task_image_id'] > 0


def test_annotated_gives_review_entry_and_stage_filter(client, user, scene):
    project_id = scene['project']['id']
    task = create_task(client, user, project_id, scene['images'][:1])
    submit_task(client, user, project_id, task['id'])
    detail = task_detail(client, user, project_id, task['id'])
    row = detail['images'][0]
    annotate_image(client, user, project_id, task['id'], row['id'], [(10, 10, 40, 30)])
    complete_task(client, user, project_id, task['id'])

    annotated = project_images(client, user, project_id, stage='annotated')
    assert annotated['total'] == 1
    item = annotated['items'][0]
    assert item['stage'] == 'annotated'
    assert item['annotated'] is True
    assert item['can_review'] is True
    assert item['can_annotate'] is False

    # 另一张没进任务，仍然只出现在「全部」里
    everything = project_images(client, user, project_id)
    stages = {row['id']: row['stage'] for row in everything['items']}
    assert sorted(stages.values()) == ['annotated', 'none']


def test_rejected_image_wins_over_reviewable(client, user, scene):
    """一张图在一个任务里被驳回、在另一个任务里还能审时，列表要显示「已退回」。"""
    project_id = scene['project']['id']
    image_id = scene['images'][0]

    first = create_task(client, user, project_id, [image_id], name='先标')
    submit_task(client, user, project_id, first['id'])
    detail = task_detail(client, user, project_id, first['id'])
    annotate_image(client, user, project_id, first['id'], detail['images'][0]['id'], [(5, 5, 30, 30)])
    complete_task(client, user, project_id, first['id'])
    rejected = review_task_image(
        client, user, project_id, first['id'], detail['images'][0]['id'], approved=False, reason='框偏了'
    )
    assert rejected.status_code == 200, rejected.text

    page = project_images(client, user, project_id, stage='rejected')
    assert page['total'] == 1
    assert page['items'][0]['review_status'] == 'rejected'


def test_invalid_stage_is_rejected(client, user, scene):
    response = client.get(
        f"/projects/{scene['project']['id']}/images",
        params={'stage': 'bogus'},
        headers=user.headers,
    )
    assert response.status_code == 400
    detail = response.json()['detail']
    assert 'stage 必须是' in detail
    for value in ('none', 'pending', 'annotated', 'approved', 'rejected', 'failed'):
        assert value in detail


def test_limit_and_offset_still_work_with_filter(client, user, scene):
    project_id = scene['project']['id']
    upload_images(client, user, project_id, count=3)
    first = project_images(client, user, project_id, limit=2, offset=0)
    second = project_images(client, user, project_id, limit=2, offset=2)
    assert first['total'] == 5 and len(first['items']) == 2
    assert second['total'] == 5
    ids = {row['id'] for row in first['items']} | {row['id'] for row in second['items']}
    assert len(ids) == 4
