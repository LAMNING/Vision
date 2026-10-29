from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.data import Account
from app.data.crud.revoked_token import is_revoked
from app.database import req_db
from app.errors import PlatformAdminRequiredError
from app.utils.security import TokenPayload, decode_access_token

DBSession = Annotated[Session, Depends(req_db)]


oauth2_scheme = OAuth2PasswordBearer(tokenUrl='/auth/signin/form')


def unauthorized() -> HTTPException:
    '''统一的 401；不区分「用户不存在」「token 过期」「签名错」。'''
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail='登录状态无效或已过期',
        headers={'WWW-Authenticate': 'Bearer'},
    )


def get_current_claims(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: DBSession,
) -> TokenPayload:
    '''只校验 token 本身：签名、有效期、是否在登出黑名单里。'''
    claims = decode_access_token(token)
    if claims is None or is_revoked(db, claims.jti):
        raise unauthorized()
    return claims


def get_current_account(
    claims: Annotated[TokenPayload, Depends(get_current_claims)],
    db: DBSession,
) -> Account:
    '''再加一层账号校验：账号还在、token 版本没被改密顶掉。'''
    account = db.get(Account, claims.account_id)
    if account is None or account.token_version != claims.token_version:
        raise unauthorized()

    return account


# 需要 jti（登出）时用 CurrentClaims
CurrentClaims = Annotated[TokenPayload, Depends(get_current_claims)]
CurrentAccount = Annotated[Account, Depends(get_current_account)]


def require_platform_admin(account: CurrentAccount) -> Account:
    '''平台管理员守卫：platform_role 不是 'admin' 直接 403。'''
    if account.platform_role != 'admin':
        raise PlatformAdminRequiredError()
    return account


# 平台级管理员接口用 PlatformAdmin（比 CurrentAccount 多一道角色检查）
PlatformAdmin = Annotated[Account, Depends(require_platform_admin)]
