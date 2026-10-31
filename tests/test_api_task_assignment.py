"""指派与复核人：建任务时选的「指派标注员 / 复核人」必须真的生效。

界面之前没有这两个选择器（`TaskCreate` 早就支持 `assignee_account_id` 与
`reviewer_account_id`），本轮补上之后，这里把后端的口径钉住：
- 指派后任务出现在被指派人的 `assigned_to_me` 列表里，别人看不到；
- 指定了复核人，就只有那个人（或 manager）能裁决。
"""

from conftest import (
    add_member,
    annotate_image,
    complete_task,
    create_task,
    make_category,
    make_project,
    project_tasks,
    review_task_image,
    signup,
    submit_task,
    task_detail,
    upload_images,
    whoami,
)


def _ready_task(client, owner, *, assignee=None, reviewer=None):
    """建项目 + 类目 + 一张图 + 一个已提交的人工任务，返回 (项目, 任务, task_image)。"""
    project = make_project(client, owner)
    make_category(client, owner, project['id'])
    image_ids = upload_images(client, owner, project['id'], 1)
    task = create_task(
        client,
        owner,
        project['id'],
        image_ids,
        assignee_account_id=assignee,
        reviewer_account_id=reviewer,
    )
    detail = task_detail(client, owner, project['id'], task['id'])
    return project, task, detail['images'][0]


def test_assignee_shows_up_in_that_members_list(client, user):
    project = make_project(client, user)
    make_category(client, user, project['id'])
    image_ids = upload_images(client, user, project['id'], 1)

    other = signup(client, 'ann')
    other_id = whoami(client, other)['id']
    add_member(client, user, project['id'], other_id, 'annotator')

    task = create_task(
        client, user, project['id'], image_ids, assignee_account_id=other_id
    )
    assert task['assignee_account_id'] == other_id

    # 被指派人：只看我的能拿到
    mine = project_tasks(client, other, project['id'], assigned_to_me='true')
    assert [row['id'] for row in mine] == [task['id']]
    # 提交人自己不是负责人，只看我的应为空
    assert project_tasks(client, user, project['id'], assigned_to_me='true') == []
    # 显式按指派人过滤也一致
    filtered = project_tasks(client, user, project['id'], assignee_account_id=other_id)
    assert [row['id'] for row in filtered] == [task['id']]


def test_pinned_reviewer_is_the_only_one_who_can_decide(client, user):
    reviewer = signup(client, 'rev')
    reviewer_id = whoami(client, reviewer)['id']
    other_reviewer = signup(client, 'rev2')
    other_reviewer_id = whoami(client, other_reviewer)['id']

    project = make_project(client, user)
    make_category(client, user, project['id'])
    add_member(client, user, project['id'], reviewer_id, 'reviewer')
    add_member(client, user, project['id'], other_reviewer_id, 'reviewer')
    image_ids = upload_images(client, user, project['id'], 1)

    task = create_task(
        client, user, project['id'], image_ids, reviewer_account_id=reviewer_id
    )
    assert task['reviewer_account_id'] == reviewer_id

    # 标注员（这里就是提交人）标完并结束，任务进入待复核
    submit_task(client, user, project['id'], task['id'])
    detail = task_detail(client, user, project['id'], task['id'])
    row = detail['images'][0]
    annotate_image(client, user, project['id'], task['id'], row['id'], [[2, 2, 20, 18]])
    complete_task(client, user, project['id'], task['id'])

    # 没被指派的那位复核员不能裁决 —— 与 review.py 的 _assert_reviewer_allowed 对齐
    blocked = client.post(
        f"/projects/{project['id']}/tasks/{task['id']}/review",
        json={'decision': 'reject_fix', 'comment': '我看一下'},
        headers=other_reviewer.headers,
    )
    assert blocked.status_code == 403, blocked.text

    # 指派的复核人逐图通过，任务自动收口
    passed = review_task_image(
        client, reviewer, project['id'], task['id'], row['id'], approved=True
    )
    assert passed.status_code == 200, passed.text
    assert task_detail(client, user, project['id'], task['id'])['status'] == 'approved'


def test_assignee_survives_submit_and_is_visible_in_detail(client, user):
    """提交（draft → queued）不该把指派人弄丢，任务行才有「负责 xxx」可显示。"""
    helper = signup(client, 'hlp')
    helper_id = whoami(client, helper)['id']
    project = make_project(client, user)
    make_category(client, user, project['id'])
    add_member(client, user, project['id'], helper_id, 'annotator')
    image_ids = upload_images(client, user, project['id'], 1)

    task = create_task(client, user, project['id'], image_ids, assignee_account_id=helper_id)
    submit_task(client, user, project['id'], task['id'])
    detail = task_detail(client, user, project['id'], task['id'])
    assert detail['status'] == 'queued'
    assert detail['assignee_account_id'] == helper_id
    # 被指派人就是任务行的负责人，标注入口也要对他开放
    from conftest import project_images

    rows = project_images(client, helper, project['id'], stage='pending')
    assert rows['items'][0]['can_annotate'] is True
