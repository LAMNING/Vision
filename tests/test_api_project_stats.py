"""项目统计的聚合口径 —— 防止「隐式笛卡尔积把每条统计放大成标注条数倍」回归。

历史问题（`app/data/crud/project_stats.py`）：三个分组查询写成
`select(...).select_from(base.subquery())`，但聚合列仍然引用 `Annotation` 实体，
SQLAlchemy 于是把 `annotation` 表再塞进 FROM 且没有连接条件 —— 每条结果被放大。
真实数据里 4 条当前标注会报成 16 条。这里的断言把口径钉死：
每个分组的计数之和必须等于 `annotations_current`。
"""

import pytest

from conftest import (
    annotate_image,
    complete_task,
    create_task,
    make_category,
    make_project,
    submit_task,
    task_detail,
    upload_images,
)


@pytest.fixture()
def scene(client, user):
    project = make_project(client, user, '统计口径')
    category = make_category(client, user, project['id'], zh_name='花', en_name='a flower')
    image_ids = upload_images(client, user, project['id'], count=3)
    return {'project': project, 'category': category, 'images': image_ids}


def test_counts_are_not_multiplied(client, user, scene):
    project_id = scene['project']['id']
    category_id = scene['category']['id']

    # 任务一：两张图，第一张 2 个框、第二张 1 个框
    first = create_task(client, user, project_id, scene['images'][:2], name='两个框')
    submit_task(client, user, project_id, first['id'])
    rows = task_detail(client, user, project_id, first['id'])['images']
    annotate_image(
        client, user, project_id, first['id'], rows[0]['id'],
        [(5, 5, 40, 40), (50, 50, 90, 90)], category_id=category_id,
    )
    annotate_image(
        client, user, project_id, first['id'], rows[1]['id'], [(8, 8, 60, 60)], category_id=category_id
    )
    complete_task(client, user, project_id, first['id'])

    # 任务二：第三张图 1 个框，不分类目
    second = create_task(client, user, project_id, scene['images'][2:], name='一个框')
    submit_task(client, user, project_id, second['id'])
    third_row = task_detail(client, user, project_id, second['id'])['images'][0]
    annotate_image(client, user, project_id, second['id'], third_row['id'], [(12, 12, 80, 70)])

    stats = client.get(f'/projects/{project_id}/stats', headers=user.headers)
    assert stats.status_code == 200, stats.text
    body = stats.json()

    assert body['annotations_current'] == 4
    assert sum(body['annotations_by_source'].values()) == 4
    assert body['annotations_by_source'] == {'manual': 4}
    assert sum(item['count'] for item in body['annotations_by_category']) == 4
    named = {item['name']: item['count'] for item in body['annotations_by_category']}
    assert named['花'] == 3
    assert any(item['category_id'] is None and item['count'] == 1 for item in body['annotations_by_category'])
    assert sum(item['count'] for item in body['annotations_by_day']) == 4

    # 其它统计同样只算一遍
    assert body['images_total'] == 3
    assert body['tasks_total'] == 2
    assert body['tasks_by_mode'] == {'manual': 2}
    assert body['reviewed_tasks'] == 0
    assert body['approved_tasks'] == 0


def test_history_revision_is_not_counted(client, user, scene):
    """改标注后旧版本保留为历史，但统计只算 `is_current` 的那一份。"""
    project_id = scene['project']['id']
    task = create_task(client, user, project_id, scene['images'][:1], name='改一遍')
    submit_task(client, user, project_id, task['id'])
    row = task_detail(client, user, project_id, task['id'])['images'][0]

    annotate_image(client, user, project_id, task['id'], row['id'], [(5, 5, 40, 40)])
    # rejected_fix 允许再改一次：revision +1，旧行 is_current=False
    complete_task(client, user, project_id, task['id'])
    reject = client.post(
        f'/projects/{project_id}/tasks/{task["id"]}/review',
        json={'decision': 'reject_fix'},
        headers=user.headers,
    )
    # 任务级退回要求至少一张图被驳回，先逐图驳回
    assert reject.status_code == 400
    assert 'cannot reject' in reject.text
    client.post(
        f'/projects/{project_id}/tasks/{task["id"]}/images/{row["id"]}/review',
        json={'approved': False, 'reason': '偏了'},
        headers=user.headers,
    )
    reject = client.post(
        f'/projects/{project_id}/tasks/{task["id"]}/review',
        json={'decision': 'reject_fix'},
        headers=user.headers,
    )
    assert reject.status_code == 200, reject.text

    annotate_image(client, user, project_id, task['id'], row['id'], [(5, 5, 44, 44), (50, 50, 90, 90)])

    stats = client.get(f'/projects/{project_id}/stats', headers=user.headers).json()
    assert stats['annotations_current'] == 2
    history = client.get(
        f'/projects/{project_id}/annotations',
        params={'is_current': 'false', 'limit': 200},
        headers=user.headers,
    ).json()
    assert history['total'] == 1
