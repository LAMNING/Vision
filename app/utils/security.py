import logging
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash

logger = logging.getLogger(__name__)

password_hasher = PasswordHash.recommended()

ALGORITHM = 'HS256'
ACCESS_TOKEN_TTL = timedelta(hours=12)
ACCESS_TOKEN_TTL_SECONDS = int(ACCESS_TOKEN_TTL.total_seconds())


def _load_secret_key() -> str:
    '''优先读环境变量；缺省时生成一次性密钥并告警（重启即失效）。'''
    key = os.environ.get('IMAGELABEL_SECRET_KEY', '').strip()
    if key:
        return key

    logger.warning(
        '环境变量 IMAGELABEL_SECRET_KEY 未设置，已生成本进程临时密钥，'
        '重启后所有已签发 token 都会失效；生产环境必须在环境变量里显式配置'
    )
    return secrets.token_urlsafe(48)


SECRET_KEY = _load_secret_key()


def hash_password(plain_password: str) -> str:
    return password_hasher.hash(plain_password)


def verify_password(
    plain_password: str, hashed_password: str
) -> tuple[bool, str | None]:
    return password_hasher.verify_and_update(plain_password, hashed_password)


@dataclass(frozen=True)
class TokenPayload:
    '''解码后的 token 内容；expires_at 是 naive UTC。'''

    account_id: int
    token_version: int
    jti: str
    expires_at: datetime


def create_access_token(
    account_id: int, token_version: int, *, ttl: timedelta = ACCESS_TOKEN_TTL
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        # pyjwt >= 2.10 校验 sub 必须是字符串，别直接传 int
        'sub': str(account_id),
        # 版本号：改密时 +1，一次性作废该账号所有 token
        'ver': token_version,
        # 单枚 token 的 id：登出时只把这一枚写进黑名单
        'jti': uuid.uuid4().hex,
        'iat': now,
        'exp': now + ttl,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> TokenPayload | None:
    '''校验签名与过期时间；任何异常都按无效 token 处理。'''
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        expires_at = datetime.fromtimestamp(
            int(payload['exp']), tz=timezone.utc
        ).replace(tzinfo=None)
        return TokenPayload(
            account_id=int(payload['sub']),
            token_version=int(payload['ver']),
            jti=str(payload['jti']),
            expires_at=expires_at,
        )
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        return None
