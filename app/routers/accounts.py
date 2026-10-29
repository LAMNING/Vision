"""账号检索：邀请/指派时按用户名或邮箱找人的轻量接口。

只暴露 id + username（+ 展示名/有无头像，供成员列表直接渲染），
避免普通用户拿到邮箱、手机号等隐私字段；
完整的账号管理仍在 /admin/accounts（平台管理员）。
"""

from typing import Annotated

from fastapi import APIRouter, Header, Query, Response
from fastapi.responses import StreamingResponse

from app.data import Account
from app.data.crud import account as account_crud
from app.deps import CurrentAccount, DBSession
from app.errors import AccountNotFoundError, NotFoundError
from app.schemas.account import AccountSummary
from app.services import storage
from app.services.unifile import FileNotFound
from app.utils.http import etag_matches

router = APIRouter(prefix='/accounts', tags=['accounts'])


@router.get('/search', response_model=list[AccountSummary])
def search_accounts(
    account: CurrentAccount,
    db: DBSession,
    q: Annotated[str, Query(min_length=2, max_length=50, description='用户名或邮箱片段')],
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
) -> list[AccountSummary]:
    '''登录用户按用户名/邮箱模糊查人（不含自己）。'''
    rows = account_crud.search_accounts(
        db, q, exclude_account_id=account.id, limit=limit
    )
    return [AccountSummary.model_validate(row) for row in rows]


@router.get('/{account_id}/avatar')
def read_avatar(
    account_id: int,
    _account: CurrentAccount,
    db: DBSession,
    if_none_match: Annotated[str | None, Header(alias='If-None-Match')] = None,
):
    '''取账号头像（登录即可看，头像本身就是给他人看的）。

    `Cache-Control: no-cache` + ETag：换头像后 key 变了，浏览器下次一定
    重新校验并拿到新图，不会出现"改完头像还是旧图"。
    '''
    target = db.get(Account, account_id)
    if target is None:
        raise AccountNotFoundError(account_id)

    key = target.avatar_key
    if not key:
        raise NotFoundError(f'account {account_id} has no avatar')

    etag = f'"{key}"'
    headers = {
        'ETag': etag,
        'Cache-Control': 'private, no-cache',
        'Content-Disposition': 'inline',
        'Content-Type': 'image/jpeg',
    }
    if etag_matches(if_none_match, etag):
        return Response(status_code=304, headers=headers)

    info = storage.stat_info(key)
    if info is None:
        raise NotFoundError(f'avatar blob of account {account_id} missing in storage')

    headers['Content-Length'] = str(info.size)
    try:
        stream = storage.open_binary(key)
    except FileNotFound:
        raise NotFoundError(f'avatar blob of account {account_id} missing in storage') from None

    return StreamingResponse(
        storage.iter_chunks(stream),
        media_type='image/jpeg',
        headers=headers,
    )
