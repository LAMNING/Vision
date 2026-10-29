from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.data.account import Account
from app.data.crud.group import create_personal_group
from app.errors import ConflictError, ValidationError
from app.services import storage
from app.services.image_upload import prepare_avatar
from app.utils.security import hash_password, verify_password


def create_account(db: Session, *, username: str, password: str, **kw) -> Account:
    acc = Account(username=username, password=hash_password(password), **kw)
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return acc


def search_accounts(
    db: Session,
    query: str,
    *,
    exclude_account_id: int | None = None,
    limit: int = 10,
) -> list[Account]:
    '''按用户名/邮箱前缀模糊查账号，供邀请、项目成员等场景选人。

    只返回摘要字段（调用方用 AccountSummary），不做全量列举。
    '''
    q = (query or '').strip()
    if len(q) < 2:
        raise ValidationError('搜索关键字至少 2 个字符')
    pattern = f'%{q}%'
    stmt = (
        select(Account)
        .where(
            Account.username.ilike(pattern)
            | (Account.email.is_not(None) & Account.email.ilike(pattern))
        )
        .order_by(Account.username, Account.id)
        .limit(max(1, min(limit, 20)))
    )
    if exclude_account_id is not None:
        stmt = stmt.where(Account.id != exclude_account_id)
    return list(db.scalars(stmt).all())



def register_account(
    db: Session,
    *,
    username: str,
    password: str,
    email: str | None = None,
    phone: str | None = None,
) -> Account:
    if db.scalar(select(Account.id).where(Account.username == username)) is not None:
        raise ConflictError(f'username {username!r} already taken')
    if email and db.scalar(select(Account.id).where(Account.email == email)) is not None:
        raise ConflictError(f'email {email!r} already registered')
    if phone and db.scalar(select(Account.id).where(Account.phone == phone)) is not None:
        raise ConflictError(f'phone {phone!r} already registered')

    acc = Account(
        username=username,
        password=hash_password(password),
        email=email,
        phone=phone,
    )
    db.add(acc)
    db.flush()

    create_personal_group(db, account=acc, commit=False)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ConflictError('username / email / phone already exists') from None
    db.refresh(acc)
    return acc


def verify_account(db: Session, *, username: str, password: str) -> Account | None:
    acc = db.scalar(select(Account).where(Account.username == username))
    if not acc: return None

    passed, new_hash = verify_password(password, acc.password)
    if not passed: return None

    if new_hash:
        acc.password = new_hash
        try: db.commit()
        except Exception: db.rollback()
    return acc


def update_profile(db: Session, account: Account, **fields) -> Account:
    allowed = {'email', 'phone', 'display_name'}
    unknown = set(fields) - allowed
    if unknown:
        raise ValidationError(f'unexpected fields: {sorted(unknown)}')

    # 展示名清空的两种写法（"" 与 null）统一落成 NULL，免得库里有空串
    if 'display_name' in fields and not fields['display_name']:
        fields['display_name'] = None

    new_email = fields.get('email')
    if new_email and db.scalar(
        select(Account.id).where(
            Account.email == new_email, Account.id != account.id
        )
    ) is not None:
        raise ConflictError(f'email {new_email!r} already registered')

    new_phone = fields.get('phone')
    if new_phone and db.scalar(
        select(Account.id).where(
            Account.phone == new_phone, Account.id != account.id
        )
    ) is not None:
        raise ConflictError(f'phone {new_phone!r} already registered')

    for key, value in fields.items():
        setattr(account, key, value)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ConflictError('email / phone already registered') from None
    db.refresh(account)
    return account


def change_password(
    db: Session, account: Account, *, old_password: str, new_password: str
) -> Account:
    passed, _ = verify_password(old_password, account.password)
    if not passed:
        raise ValidationError('旧密码不正确')

    account.password = hash_password(new_password)
    account.token_version += 1
    db.commit()
    db.refresh(account)
    return account


def set_avatar(db: Session, account: Account, data: bytes) -> Account:
    '''替换头像：先写存储再落库，落库失败就删掉刚写的对象。

    数据库只存对象路径，换新图时旧对象在后一步删；顺序反过来的话，
    提交失败会留下"库里有 key、存储里没文件"的坏状态。
    '''
    payload = prepare_avatar(data)
    key = storage.build_avatar_key(account.id)
    storage.save_bytes(key, payload)

    old_key = account.avatar_key
    account.avatar_key = key
    try:
        db.commit()
    except Exception:
        db.rollback()
        storage.delete(key)
        raise
    db.refresh(account)

    if old_key and old_key != key:
        storage.delete(old_key)
    return account


def clear_avatar(db: Session, account: Account) -> Account:
    '''移除头像；本来就没有时直接返回，保证幂等。'''
    old_key = account.avatar_key
    if not old_key:
        return account

    account.avatar_key = None
    db.commit()
    db.refresh(account)
    storage.delete(old_key)
    return account


def delete_account(db: Session, account_id: int) -> bool:
    acc = db.get(Account, account_id)
    if not acc: return False
    db.delete(acc)
    db.commit()
    return True
