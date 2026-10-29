from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm

from app.data import Account
from app.data.crud.account import (
    change_password,
    register_account,
    update_profile,
    verify_account,
)
from app.data.crud.revoked_token import revoke
from app.deps import CurrentAccount, CurrentClaims, DBSession
from app.schemas.account import (
    AccountCreate,
    AccountRead,
    AccountUpdate,
    PasswordChange,
)
from app.schemas.auth import LoginRequest, TokenResponse
from app.services import rate_limit
from app.utils.security import ACCESS_TOKEN_TTL_SECONDS, create_access_token

router = APIRouter(prefix='/auth', tags=['auth'])


def _client_key(request: Request) -> str:
    '''限流 key：真实客户端 IP（反向代理后要靠 uvicorn --proxy-headers）。'''
    client = request.client
    return client.host if client is not None else 'unknown'


def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail='用户名或密码错误',
        headers={'WWW-Authenticate': 'Bearer'},
    )


def _issue_token(account: Account) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(account.id, account.token_version),
        expires_in=ACCESS_TOKEN_TTL_SECONDS,
        account=account,
    )


@router.post(
    '/signup',
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
)
def signup(payload: AccountCreate, request: Request, db: DBSession) -> TokenResponse:
    rate_limit.check('signup', _client_key(request))
    account = register_account(
        db,
        username=payload.username,
        password=payload.password,
        email=payload.email,
        phone=payload.phone,
    )
    return _issue_token(account)


@router.post('/signin', response_model=TokenResponse)
def signin(
    payload: LoginRequest, request: Request, db: DBSession
) -> TokenResponse:
    key = _client_key(request)
    rate_limit.check('signin', key)
    account = verify_account(
        db, username=payload.username, password=payload.password
    )
    if account is None:
        raise _invalid_credentials()
    rate_limit.clear('signin', key)
    return _issue_token(account)


@router.post('/signin/form', response_model=TokenResponse)
def signin_form(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    request: Request,
    db: DBSession,
) -> TokenResponse:
    '''OAuth2 密码模式（form-encoded）
    '''
    key = _client_key(request)
    rate_limit.check('signin', key)
    account = verify_account(
        db, username=form.username, password=form.password
    )
    if account is None:
        raise _invalid_credentials()
    rate_limit.clear('signin', key)
    return _issue_token(account)


@router.post('/signout', status_code=status.HTTP_204_NO_CONTENT)
def signout(claims: CurrentClaims, db: DBSession) -> None:
    revoke(
        db,
        jti=claims.jti,
        account_id=claims.account_id,
        expires_at=claims.expires_at,
    )


@router.get('/me', response_model=AccountRead)
def read_me(account: CurrentAccount) -> Account:
    return account


@router.patch('/me', response_model=AccountRead)
def update_me(
    payload: AccountUpdate, account: CurrentAccount, db: DBSession
) -> Account:
    return update_profile(db, account, **payload.model_dump(exclude_unset=True))


@router.post('/me/password', status_code=status.HTTP_204_NO_CONTENT)
def change_my_password(
    payload: PasswordChange, account: CurrentAccount, db: DBSession
) -> None:
    '''改密成功后本 token 与其它设备的 token 一并失效，需要重新登录。'''
    change_password(
        db,
        account,
        old_password=payload.old_password,
        new_password=payload.new_password,
    )
