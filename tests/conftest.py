"""测试基线。

这里必须在**导入 app 之前**把环境变量设好：`app/database.py` 在 import 时就用
`IMAGELABEL_DATABASE_URL` 建好了 engine，之后改环境变量不会再生效。
pytest 会先导入 conftest，再导入各测试模块，所以模块级的赋值是可靠的。

测试库与存储都放在系统临时目录，跑完不影响仓库里的 `test.db` / `state/`。
"""

import io
import itertools
import os
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

_TMP = Path(tempfile.mkdtemp(prefix='lmvision-tests-'))
_DB_PATH = _TMP / 'test.db'
_STORAGE = _TMP / 'storage'

os.environ['IMAGELABEL_DATABASE_URL'] = 'sqlite:///' + _DB_PATH.as_posix()
os.environ['IMAGELABEL_STORAGE_URI'] = _STORAGE.as_posix()
os.environ['IMAGELABEL_SECRET_KEY'] = 'test-secret-key-0123456789abcdef0123456789'
# 后台线程（自动标注 / 到期提醒）在测试里没有意义，关掉免得干扰断言与日志
os.environ['IMAGELABEL_AUTO_WORKER'] = '0'
os.environ['IMAGELABEL_REMINDER_WORKER'] = '0'
os.environ['IMAGELABEL_AUTO_MIGRATE'] = '0'
# 每个用例都注册一个新账号，默认 10 次/分的登录限流会挡住测试；窗口设成 1 秒也够
os.environ['IMAGELABEL_SIGNIN_LIMIT'] = '10000'
os.environ['IMAGELABEL_SIGNIN_WINDOW'] = '1'

PASSWORD = 'Passw0rd!234'


@pytest.fixture(scope='session')
def client():
    """带 lifespan 的 TestClient（会把表建好、起停 worker）。"""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean_db(client):
    """每个用例前清空业务表，用例之间互不干扰。

    `platform_setting` 是单行配置（lifespan 启动时读过一次），保留它能避免
    管理员相关接口拿到 None；其它表按外键反序清空。
    """
    from app import database

    keep = {'platform_setting'}
    with database.SessionLocal() as db:
        for table in reversed(database.BasicModel.metadata.sorted_tables):
            if table.name in keep:
                continue
            db.execute(table.delete())
        db.commit()
    yield


def png_bytes(width=48, height=32, color=(180, 60, 60)):
    """生成一张真实可解码的 PNG，上传接口会校验并生成缩略图。"""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new('RGB', (width, height), color).save(buffer, format='PNG')
    return buffer.getvalue()


_SEQ = itertools.count(1)


def _color():
    """每次调用给一个不同的颜色，避免上传去重把两张图合成一张。"""
    index = next(_SEQ)
    return (20 + (index * 37) % 200, 60 + (index * 53) % 180, 90 + (index * 29) % 160)


def signup(client, prefix='u'):
    """注册一个新账号，返回 token / 请求头 / 个人空间 id。"""
    username = prefix + uuid.uuid4().hex[:10]
    response = client.post('/auth/signup', json={'username': username, 'password': PASSWORD})
    assert response.status_code in (200, 201), response.text
    body = response.json()
    token = body['access_token']
    headers = {'Authorization': f'Bearer {token}'}
    space = client.get('/space', headers=headers)
    assert space.status_code == 200, space.text
    return SimpleNamespace(
        username=username,
        token=token,
        headers=headers,
        group_id=space.json()['id'],
    )


@pytest.fixture()
def user(client):
    return signup(client)


def make_project(client, user, name='测试项目'):
    response = client.post('/space/projects', json={'name': name}, headers=user.headers)
    assert response.status_code in (200, 201), response.text
    return response.json()


def make_category(client, user, project_id, zh_name='树', en_name='a tree'):
    response = client.post(
        f'/projects/{project_id}/categories',
        json={'zh_name': zh_name, 'en_name': en_name},
        headers=user.headers,
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def upload_images(client, user, project_id, count=1, size=(48, 32)):
    # 每张图颜色不同：上传接口按 (group_id, sha256) 去重，同色会被当成同一张复用
    files = [
        ('files', (f'img-{next(_SEQ)}.png', png_bytes(*size, color=_color()), 'image/png'))
        for _ in range(count)
    ]
    response = client.post(f'/projects/{project_id}/images', files=files, headers=user.headers)
    assert response.status_code in (200, 201), response.text
    return [row['id'] for row in response.json()]


def create_task(client, user, project_id, image_ids, *, mode='manual', review_required=True,
                name='任务', assignee_account_id=None, reviewer_account_id=None):
    payload = {
        'project_id': project_id,
        'name': name,
        'mode': mode,
        'image_ids': image_ids,
        'review_required': review_required,
    }
    if assignee_account_id is not None:
        payload['assignee_account_id'] = assignee_account_id
    if reviewer_account_id is not None:
        payload['reviewer_account_id'] = reviewer_account_id
    response = client.post(
        f'/projects/{project_id}/tasks',
        json=payload,
        headers=user.headers,
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def whoami(client, user):
    """账号 id：指派 / 复核都要用到。"""
    response = client.get('/auth/me', headers=user.headers)
    assert response.status_code == 200, response.text
    return response.json()


def add_member(client, owner, project_id, account_id, role='annotator'):
    response = client.post(
        f'/projects/{project_id}/members',
        json={'account_id': account_id, 'role': role},
        headers=owner.headers,
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def task_detail(client, user, project_id, task_id):
    response = client.get(f'/projects/{project_id}/tasks/{task_id}', headers=user.headers)
    assert response.status_code == 200, response.text
    return response.json()


def annotate_image(client, user, project_id, task_id, task_image_id, boxes, category_id=None):
    items = [
        {
            'label': '树',
            'category_id': category_id,
            'annotation_type': 'bbox',
            'payload': {'box': list(box)},
            'confidence': None,
        }
        for box in boxes
    ]
    response = client.put(
        f'/projects/{project_id}/tasks/{task_id}/images/{task_image_id}/annotations',
        json={'items': items, 'source': 'manual'},
        headers=user.headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def submit_task(client, user, project_id, task_id):
    response = client.post(f'/projects/{project_id}/tasks/{task_id}/submit', headers=user.headers)
    assert response.status_code == 200, response.text
    return response.json()


def complete_task(client, user, project_id, task_id):
    response = client.post(f'/projects/{project_id}/tasks/{task_id}/complete', headers=user.headers)
    assert response.status_code == 200, response.text
    return response.json()


def review_task_image(client, user, project_id, task_id, task_image_id, *, approved, reason=None):
    payload = {'approved': approved}
    if reason is not None:
        payload['reason'] = reason
    return client.post(
        f'/projects/{project_id}/tasks/{task_id}/images/{task_image_id}/review',
        json=payload,
        headers=user.headers,
    )


def project_images(client, user, project_id, **params):
    response = client.get(f'/projects/{project_id}/images', params=params, headers=user.headers)
    assert response.status_code == 200, response.text
    return response.json()


def project_tasks(client, user, project_id, **params):
    response = client.get(f'/projects/{project_id}/tasks', params=params, headers=user.headers)
    assert response.status_code == 200, response.text
    return response.json()
