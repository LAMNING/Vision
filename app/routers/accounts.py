"""账号检索：邀请/指派时按用户名或邮箱找人的轻量接口。

只暴露 id + username，避免普通用户拿到邮箱、手机号等隐私字段；
完整的账号管理仍在 /admin/accounts（平台管理员）。
"""

from typing import Annotated

from fastapi import APIRouter, Query

from app.data.crud import account as account_crud
from app.deps import CurrentAccount, DBSession
from app.schemas.account import AccountSummary

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
