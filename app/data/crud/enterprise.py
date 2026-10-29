'''企业空间的业务逻辑：成员与角色、凭码加入、图库审核、配额、公告、日报。

几条固定约定：
- 企业图仍然是 ``Image``，落在企业背后的隐藏 ``Group`` 里，复用去重与缩略图；
- 在线状态不落库，按 ``last_seen_at``（45 秒内）与 ``invisible`` 现算；
- 所有写操作顺手写一条 ``EnterpriseLog``，``detail`` 是给用户看的中文句子。
'''

from __future__ import annotations

import secrets
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.data import Account, Group, Image
from app.data.crud import image as image_crud
from app.data.enterprise import (
    ASSIGNABLE_ROLES,
    ENTERPRISE_ROLES,
    ROLE_RANK,
    Enterprise,
    EnterpriseImage,
    EnterpriseJoinRequest,
    EnterpriseLog,
    EnterpriseMember,
)
from app.errors import (
    ConflictError,
    EnterpriseMemberNotFoundError,
    EnterpriseNotFoundError,
    EnterpriseQuotaError,
    EnterpriseRoleError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.services import storage
from app.services.image_upload import delete_variants
from app.utils.time import utcnow

ONLINE_WINDOW_SECONDS = 45
LOG_PAGE_SIZE = 50
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # 去掉了 I/O/0/1，便于口头转述
CODE_LENGTH = 8

# 企业必须用企业邮箱创建：免费邮箱域名一律拒绝
FREE_EMAIL_DOMAINS = frozenset(
    {
        'gmail.com', 'googlemail.com', 'outlook.com', 'hotmail.com', 'live.com',
        'yahoo.com', 'icloud.com', 'me.com', 'aol.com', 'gmx.com', 'mail.com',
        'proton.me', 'protonmail.com', 'yandex.com',
        'qq.com', 'foxmail.com', '163.com', '126.com', 'yeah.net',
        'sina.com', 'sina.cn', 'sohu.com', 'aliyun.com', '139.com',
    }
)

ROLE_LABELS = {
    'owner': '所有者',
    'admin': '管理员',
    'reviewer': '审核员',
    'member': '成员',
    'guest': '只读访客',
}


# ---- 读取与权限 ----

def get_enterprise(db: Session, enterprise_id: int) -> Enterprise | None:
    return db.get(Enterprise, enterprise_id)


def get_enterprise_or_raise(db: Session, enterprise_id: int) -> Enterprise:
    enterprise = get_enterprise(db, enterprise_id)
    if enterprise is None:
        raise EnterpriseNotFoundError(enterprise_id)
    return enterprise


def get_membership(
    db: Session, enterprise_id: int, account_id: int
) -> EnterpriseMember | None:
    return db.scalar(
        select(EnterpriseMember).where(
            EnterpriseMember.enterprise_id == enterprise_id,
            EnterpriseMember.account_id == account_id,
        )
    )


def require_membership(
    db: Session,
    enterprise_id: int,
    account_id: int,
    *,
    minimum: str | None = None,
) -> tuple[Enterprise, EnterpriseMember]:
    '''企业不存在与非成员都返回 404：不让外部探测企业是否存在。

    ``minimum`` 为 None 表示只要是成员就行（guest 也算，只读接口用它）。
    '''
    enterprise = get_enterprise_or_raise(db, enterprise_id)
    member = get_membership(db, enterprise_id, account_id)
    if member is None:
        raise EnterpriseNotFoundError(enterprise_id)
    if minimum is not None and ROLE_RANK[member.role] < ROLE_RANK[minimum]:
        raise EnterpriseRoleError(ROLE_LABELS[minimum])
    return enterprise, member


def account_can_view_group(db: Session, group_id: int, account_id: int) -> bool:
    '''给 ``/images/{id}/content`` 用：企业成员也能取企业图。'''
    return enterprise_role_for_group(db, group_id, account_id) is not None


def enterprise_role_for_group(
    db: Session, group_id: int, account_id: int
) -> str | None:
    '''企业背后的隐藏组 → 该账号在企业里的角色；不是企业成员返回 None。'''
    stmt = (
        select(EnterpriseMember.role)
        .join(Enterprise, Enterprise.id == EnterpriseMember.enterprise_id)
        .where(
            Enterprise.group_id == group_id,
            EnterpriseMember.account_id == account_id,
        )
        .limit(1)
    )
    return db.scalar(stmt)


def online_of(member: EnterpriseMember) -> bool:
    if member.invisible or member.last_seen_at is None:
        return False
    return member.last_seen_at >= utcnow() - timedelta(seconds=ONLINE_WINDOW_SECONDS)


# ---- 统计 ----

def list_my_enterprises(db: Session, account_id: int) -> list[tuple[Enterprise, str]]:
    '''我加入的企业 + 我的角色，按加入顺序。'''
    rows = db.execute(
        select(Enterprise, EnterpriseMember.role)
        .join(EnterpriseMember, EnterpriseMember.enterprise_id == Enterprise.id)
        .where(EnterpriseMember.account_id == account_id)
        .order_by(Enterprise.id)
    ).all()
    return [(enterprise, role) for enterprise, role in rows]


def count_members(db: Session, enterprise_id: int) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(EnterpriseMember)
            .where(EnterpriseMember.enterprise_id == enterprise_id)
        )
        or 0
    )


def image_stats(db: Session, enterprise_id: int) -> tuple[int, int, int]:
    '''返回 (图片数, 占用字节, 待审数)；软删的图不计。'''
    count, used_bytes = db.execute(
        select(
            func.count(EnterpriseImage.id),
            func.coalesce(func.sum(Image.size_bytes), 0),
        )
        .join(Image, Image.id == EnterpriseImage.image_id)
        .where(
            EnterpriseImage.enterprise_id == enterprise_id,
            Image.deleted_at.is_(None),
        )
    ).one()
    pending = int(
        db.scalar(
            select(func.count())
            .select_from(EnterpriseImage)
            .join(Image, Image.id == EnterpriseImage.image_id)
            .where(
                EnterpriseImage.enterprise_id == enterprise_id,
                EnterpriseImage.review_status == 'pending',
                Image.deleted_at.is_(None),
            )
        )
        or 0
    )
    return int(count), int(used_bytes), pending


def count_pending_requests(db: Session, enterprise_id: int) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(EnterpriseJoinRequest)
            .where(
                EnterpriseJoinRequest.enterprise_id == enterprise_id,
                EnterpriseJoinRequest.status == 'pending',
            )
        )
        or 0
    )


# ---- 创建 / 修改 / 解散 ----

def _assert_enterprise_email(email: str) -> None:
    domain = email.rsplit('@', 1)[-1].lower()
    if domain in FREE_EMAIL_DOMAINS:
        raise ValidationError('请使用企业邮箱，免费邮箱不能创建企业')


def _new_code(db: Session) -> str:
    for _ in range(20):
        body = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        code = f'VS-{body}'
        if db.scalar(select(Enterprise.id).where(Enterprise.code == code)) is None:
            return code
    raise ConflictError('企业码生成失败，请重试')


def _log(
    db: Session,
    enterprise_id: int,
    actor_account_id: int | None,
    action: str,
    detail: str | None,
) -> EnterpriseLog:
    entry = EnterpriseLog(
        enterprise_id=enterprise_id,
        actor_account_id=actor_account_id,
        action=action,
        detail=(detail or '')[:255] or None,
    )
    db.add(entry)
    return entry


def record_log(
    db: Session,
    enterprise_id: int,
    actor_account_id: int | None,
    action: str,
    detail: str | None,
) -> EnterpriseLog:
    '''写一条操作留痕（不提交，跟调用方的事务一起落库）。'''
    return _log(db, enterprise_id, actor_account_id, action, detail)


def create_enterprise(
    db: Session,
    *,
    owner: Account,
    name: str,
    email: str,
    quota_images: int,
    quota_members: int,
) -> Enterprise:
    _assert_enterprise_email(email)

    # 企业图需要 group_id，建一个不写 GroupMember 的隐藏组：
    # 额度关掉（0=不限量），由企业自己的 quota_images 控制，避免两套配额打架。
    group = Group(
        name=f'企业：{name}'[:50],
        type='org',
        parent_id=None,
        owner_account_id=owner.id,
        quota_storage_mb=0,
        quota_images=0,
        quota_members=0,
        quota_source='own',
    )
    db.add(group)
    db.flush()

    enterprise = Enterprise(
        name=name,
        code=_new_code(db),
        email=email,
        owner_account_id=owner.id,
        group_id=group.id,
        quota_images=quota_images,
        quota_members=quota_members,
    )
    db.add(enterprise)
    db.flush()

    db.add(
        EnterpriseMember(
            enterprise_id=enterprise.id,
            account_id=owner.id,
            role='owner',
            last_seen_at=utcnow(),
        )
    )
    _log(db, enterprise.id, owner.id, 'enterprise.created', f'创建企业「{name}」')

    db.commit()
    db.refresh(enterprise)
    return enterprise


def update_enterprise(
    db: Session,
    enterprise: Enterprise,
    actor_account_id: int,
    **fields,
) -> Enterprise:
    allowed = {
        'name',
        'quota_images',
        'quota_members',
        'auto_approve_staff',
        'join_mode',
        'notice_text',
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValidationError(f'unexpected fields: {sorted(unknown)}')

    if fields.get('name') and fields['name'] != enterprise.name:
        enterprise.name = fields['name']
        group = db.get(Group, enterprise.group_id)
        if group is not None:
            group.name = f'企业：{fields["name"]}'[:50]
        _log(db, enterprise.id, actor_account_id, 'enterprise.renamed', f'企业改名为「{fields["name"]}」')

    for key in ('quota_images', 'quota_members', 'auto_approve_staff', 'join_mode'):
        if key in fields and fields[key] is not None:
            setattr(enterprise, key, fields[key])

    if 'notice_text' in fields:
        enterprise.notice_text = fields['notice_text'] or None
        _log(
            db,
            enterprise.id,
            actor_account_id,
            'enterprise.notice',
            '更新了企业公告' if enterprise.notice_text else '撤下了企业公告',
        )

    db.commit()
    db.refresh(enterprise)
    return enterprise


def delete_enterprise(db: Session, enterprise: Enterprise) -> None:
    '''解散：企业图连 blob 一起删，关联组软删，其余行级联清掉。'''
    links = db.scalars(
        select(EnterpriseImage).where(EnterpriseImage.enterprise_id == enterprise.id)
    ).all()
    for link in links:
        image = db.get(Image, link.image_id)
        if image is None or image.deleted_at is not None:
            continue
        image_crud.delete_image(db, image.id, commit=False)
        storage.delete(image.storage_key)
        delete_variants(image.storage_key)

    group = db.get(Group, enterprise.group_id)
    if group is not None:
        group.deleted_at = utcnow()

    db.delete(enterprise)  # members / images / requests / logs 由 cascade 清理
    db.commit()


# ---- 成员 ----

def list_members(db: Session, enterprise_id: int) -> list[tuple[EnterpriseMember, int]]:
    '''成员 + 各自上传数（软删的图不计数）。'''
    counts = dict(
        db.execute(
            select(
                EnterpriseImage.uploader_account_id,
                func.count(EnterpriseImage.id),
            )
            .join(Image, Image.id == EnterpriseImage.image_id)
            .where(
                EnterpriseImage.enterprise_id == enterprise_id,
                Image.deleted_at.is_(None),
            )
            .group_by(EnterpriseImage.uploader_account_id)
        ).all()
    )
    rows = db.scalars(
        select(EnterpriseMember)
        .where(EnterpriseMember.enterprise_id == enterprise_id)
        .order_by(EnterpriseMember.id)
    ).all()
    return [(member, int(counts.get(member.account_id, 0))) for member in rows]


def _assert_member_quota(db: Session, enterprise: Enterprise) -> None:
    used = count_members(db, enterprise.id)
    if enterprise.quota_members and used + 1 > enterprise.quota_members:
        raise EnterpriseQuotaError('企业成员名额', enterprise.quota_members, used + 1)


def add_member(
    db: Session,
    enterprise: Enterprise,
    account: Account,
    *,
    role: str = 'member',
    actor_account_id: int | None = None,
    commit: bool = True,
) -> EnterpriseMember:
    if role not in ENTERPRISE_ROLES or role == 'owner':
        raise ValidationError(f'不可直接授予角色：{role}')
    if get_membership(db, enterprise.id, account.id) is not None:
        raise ConflictError('这个账号已经是企业成员')
    _assert_member_quota(db, enterprise)

    member = EnterpriseMember(
        enterprise_id=enterprise.id,
        account_id=account.id,
        role=role,
        last_seen_at=None,
    )
    db.add(member)
    _log(
        db,
        enterprise.id,
        actor_account_id or account.id,
        'member.added',
        f'{account.username} 加入企业，角色：{ROLE_LABELS[role]}',
    )
    if commit:
        db.commit()
        db.refresh(member)
    else:
        db.flush()
    return member


def invite_member(
    db: Session,
    enterprise: Enterprise,
    *,
    username: str,
    role: str,
    actor_account_id: int,
) -> EnterpriseMember:
    account = db.scalar(select(Account).where(Account.username == username))
    if account is None:
        raise NotFoundError(f'找不到用户 {username}')
    return add_member(
        db,
        enterprise,
        account,
        role=role,
        actor_account_id=actor_account_id,
    )


def update_member(
    db: Session,
    enterprise: Enterprise,
    *,
    actor: EnterpriseMember,
    target_account_id: int,
    role: str | None = None,
    invisible: bool | None = None,
) -> EnterpriseMember:
    target = get_membership(db, enterprise.id, target_account_id)
    if target is None:
        raise EnterpriseMemberNotFoundError(target_account_id)

    if target.account_id == actor.account_id:
        # 本人只能改隐身；改角色必须由更高角色的人操作
        if role is not None:
            raise PermissionDeniedError('不能修改自己的角色')
    else:
        if target.role == 'owner':
            raise PermissionDeniedError('不能修改企业所有者的角色')
        if ROLE_RANK[actor.role] <= ROLE_RANK[target.role]:
            raise PermissionDeniedError('只能管理角色低于自己的成员')

    if role is not None:
        if role not in ASSIGNABLE_ROLES:
            raise ValidationError(f'不可授予角色：{role}')
        if target.role != role:
            target.role = role
            _log(
                db,
                enterprise.id,
                actor.account_id,
                'member.role',
                f'{target.account.username if target.account else target.account_id} 的角色改为{ROLE_LABELS[role]}',
            )

    if invisible is not None:
        target.invisible = invisible
        if invisible:
            target.last_seen_at = None

    db.commit()
    db.refresh(target)
    return target


def remove_member(
    db: Session,
    enterprise: Enterprise,
    *,
    actor: EnterpriseMember,
    target_account_id: int,
) -> None:
    target = get_membership(db, enterprise.id, target_account_id)
    if target is None:
        raise EnterpriseMemberNotFoundError(target_account_id)
    if target.role == 'owner':
        raise PermissionDeniedError('不能移除企业所有者')
    if target.account_id == actor.account_id:
        raise PermissionDeniedError('退出企业请使用退出接口')
    if ROLE_RANK[actor.role] <= ROLE_RANK[target.role]:
        raise PermissionDeniedError('只能管理角色低于自己的成员')

    username = target.account.username if target.account else target.account_id
    db.delete(target)
    _log(db, enterprise.id, actor.account_id, 'member.removed', f'移除了成员 {username}')
    db.commit()


def leave_enterprise(
    db: Session, enterprise: Enterprise, member: EnterpriseMember
) -> None:
    if member.role == 'owner':
        raise ConflictError('所有者不能退出企业，请先解散企业')
    username = member.account.username if member.account else member.account_id
    db.delete(member)
    _log(db, enterprise.id, member.account_id, 'member.left', f'{username} 退出了企业')
    db.commit()


# ---- 凭码加入 ----

def join_enterprise(
    db: Session, *, code: str, account: Account, message: str | None
) -> tuple[Enterprise, str]:
    '''返回 (企业, 'active' | 'pending')；已加入的直接算 active。'''
    enterprise = db.scalar(select(Enterprise).where(Enterprise.code == code))
    if enterprise is None:
        raise NotFoundError('企业码不存在或已失效')

    existing = get_membership(db, enterprise.id, account.id)
    if existing is not None:
        return enterprise, 'active'

    if enterprise.join_mode == 'open':
        add_member(db, enterprise, account, role='member', commit=True)
        return enterprise, 'active'

    request = db.scalar(
        select(EnterpriseJoinRequest).where(
            EnterpriseJoinRequest.enterprise_id == enterprise.id,
            EnterpriseJoinRequest.account_id == account.id,
        )
    )
    if request is None:
        request = EnterpriseJoinRequest(
            enterprise_id=enterprise.id,
            account_id=account.id,
            message=message or None,
            status='pending',
        )
        db.add(request)
    else:
        if request.status == 'pending':
            raise ConflictError('已经提交过申请，请等待管理员审批')
        request.status = 'pending'
        request.message = message or None
        request.applied_at = utcnow()
        request.decided_at = None
        request.decided_by_account_id = None

    _log(db, enterprise.id, account.id, 'join.requested', f'{account.username} 申请加入企业')
    db.commit()
    return enterprise, 'pending'


def list_requests(db: Session, enterprise_id: int) -> list[EnterpriseJoinRequest]:
    return list(
        db.scalars(
            select(EnterpriseJoinRequest)
            .where(
                EnterpriseJoinRequest.enterprise_id == enterprise_id,
                EnterpriseJoinRequest.status == 'pending',
            )
            .order_by(EnterpriseJoinRequest.id)
        ).all()
    )


def decide_request(
    db: Session,
    enterprise: Enterprise,
    *,
    account_id: int,
    decision: str,
    actor_account_id: int,
) -> EnterpriseJoinRequest:
    if decision not in ('approve', 'reject'):
        raise ValidationError(f'未知的审批结果：{decision}')

    request = db.scalar(
        select(EnterpriseJoinRequest).where(
            EnterpriseJoinRequest.enterprise_id == enterprise.id,
            EnterpriseJoinRequest.account_id == account_id,
        )
    )
    if request is None or request.status != 'pending':
        raise NotFoundError('这条申请不存在或已处理')

    account = db.get(Account, account_id)
    if account is None:
        raise NotFoundError('申请账号已不存在')

    if decision == 'approve' and get_membership(db, enterprise.id, account_id) is None:
        _assert_member_quota(db, enterprise)
        db.add(
            EnterpriseMember(
                enterprise_id=enterprise.id,
                account_id=account_id,
                role='member',
            )
        )

    request.status = 'approved' if decision == 'approve' else 'rejected'
    request.decided_at = utcnow()
    request.decided_by_account_id = actor_account_id
    _log(
        db,
        enterprise.id,
        actor_account_id,
        f'join.{request.status}',
        f'{"通过" if decision == "approve" else "拒绝"}了 {account.username} 的加入申请',
    )
    db.commit()
    db.refresh(request)
    return request


# ---- 图库 ----

def image_payload(link: EnterpriseImage, image: Image) -> dict:
    return {
        'id': link.id,
        'enterprise_id': link.enterprise_id,
        'image_id': image.id,
        'filename': image.filename,
        'width': image.width,
        'height': image.height,
        'size_bytes': image.size_bytes,
        'uploader_account_id': link.uploader_account_id,
        'review_status': link.review_status,
        'review_note': link.review_note,
        'reviewed_by_account_id': link.reviewed_by_account_id,
        'reviewed_at': link.reviewed_at,
        'created_at': link.created_at,
    }


def list_images(
    db: Session,
    enterprise_id: int,
    *,
    offset: int = 0,
    limit: int = 50,
    review_status: str | None = None,
) -> tuple[list[dict], int]:
    where = [
        EnterpriseImage.enterprise_id == enterprise_id,
        Image.deleted_at.is_(None),
    ]
    if review_status is not None:
        where.append(EnterpriseImage.review_status == review_status)

    total = int(
        db.scalar(
            select(func.count())
            .select_from(EnterpriseImage)
            .join(Image, Image.id == EnterpriseImage.image_id)
            .where(*where)
        )
        or 0
    )
    rows = db.execute(
        select(EnterpriseImage, Image)
        .join(Image, Image.id == EnterpriseImage.image_id)
        .where(*where)
        .order_by(EnterpriseImage.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return [image_payload(link, image) for link, image in rows], total


def get_image_link(
    db: Session, enterprise_id: int, image_id: int
) -> EnterpriseImage | None:
    return db.scalar(
        select(EnterpriseImage).where(
            EnterpriseImage.enterprise_id == enterprise_id,
            EnterpriseImage.image_id == image_id,
        )
    )


def link_image(
    db: Session,
    *,
    enterprise_id: int,
    image_id: int,
    uploader_account_id: int,
    review_status: str,
    commit: bool = True,
) -> tuple[EnterpriseImage, bool]:
    '''把 Image 挂到企业上；重复上传命中同一张图时复用原关联。'''
    existing = get_image_link(db, enterprise_id, image_id)
    if existing is not None:
        return existing, False

    link = EnterpriseImage(
        enterprise_id=enterprise_id,
        image_id=image_id,
        uploader_account_id=uploader_account_id,
        review_status=review_status,
    )
    db.add(link)
    if commit:
        db.commit()
        db.refresh(link)
    else:
        db.flush()
    return link, True


def assert_image_quota(
    db: Session, enterprise: Enterprise, adding: int
) -> None:
    used = image_stats(db, enterprise.id)[0]
    if enterprise.quota_images and used + adding > enterprise.quota_images:
        raise EnterpriseQuotaError('企业图片配额', enterprise.quota_images, used + adding)


def initial_review_status(enterprise: Enterprise, role: str) -> str:
    '''开了 auto_approve_staff 时，管理员及以上上传的图直接通过。'''
    if enterprise.auto_approve_staff and ROLE_RANK[role] >= ROLE_RANK['admin']:
        return 'approved'
    return 'pending'


def review_image(
    db: Session,
    enterprise: Enterprise,
    *,
    image_id: int,
    decision: str,
    note: str | None,
    reviewer_account_id: int,
) -> dict:
    link = get_image_link(db, enterprise.id, image_id)
    if link is None:
        raise NotFoundError('这张图片不在企业图库里')

    status = 'approved' if decision == 'approve' else 'rejected'
    link.review_status = status
    link.review_note = note or None
    link.reviewed_by_account_id = reviewer_account_id
    link.reviewed_at = utcnow()

    image = db.get(Image, image_id)
    _log(
        db,
        enterprise.id,
        reviewer_account_id,
        f'image.{status}',
        f'{"通过" if status == "approved" else "驳回"}了图片 {image.filename if image else image_id}',
    )
    db.commit()
    db.refresh(link)
    if image is None:
        raise NotFoundError('图片已不存在')
    return image_payload(link, image)


def delete_image(
    db: Session,
    enterprise: Enterprise,
    *,
    image_id: int,
    actor_account_id: int,
) -> None:
    '''删企业图：移除关联 + 软删 Image + 清 blob 与缩略图。'''
    link = get_image_link(db, enterprise.id, image_id)
    if link is None:
        raise NotFoundError('这张图片不在企业图库里')
    image = db.get(Image, image_id)
    if image is None:
        raise NotFoundError('图片已不存在')

    filename = image.filename
    db.delete(link)
    if image.deleted_at is None:
        image_crud.delete_image(db, image.id, commit=False)
        storage.delete(image.storage_key)
        delete_variants(image.storage_key)

    _log(db, enterprise.id, actor_account_id, 'image.deleted', f'删除了图片 {filename}')
    db.commit()


# ---- 上线状态 / 排名 / 日志 / 日报 ----

def heartbeat(
    db: Session, member: EnterpriseMember, *, invisible: bool
) -> EnterpriseMember:
    member.last_seen_at = utcnow()
    member.invisible = invisible
    db.commit()
    db.refresh(member)
    return member


def ranking(db: Session, enterprise_id: int) -> list[dict]:
    rows = list_members(db, enterprise_id)
    rows.sort(key=lambda item: (-item[1], item[0].id))
    out = []
    for index, (member, count) in enumerate(rows, start=1):
        out.append(
            {
                'account_id': member.account_id,
                'username': member.account.username if member.account else '',
                'role': member.role,
                'count': count,
                'rank': index,
            }
        )
    return out


def list_logs(
    db: Session, enterprise_id: int, *, offset: int = 0, limit: int = LOG_PAGE_SIZE
) -> tuple[list[dict], int]:
    total = int(
        db.scalar(
            select(func.count())
            .select_from(EnterpriseLog)
            .where(EnterpriseLog.enterprise_id == enterprise_id)
        )
        or 0
    )
    rows = db.execute(
        select(EnterpriseLog, Account)
        .outerjoin(Account, Account.id == EnterpriseLog.actor_account_id)
        .where(EnterpriseLog.enterprise_id == enterprise_id)
        .order_by(EnterpriseLog.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return [
        {
            'id': log.id,
            'actor_account_id': log.actor_account_id,
            'actor_name': (
                (account.display_name or account.username) if account else None
            ),
            'action': log.action,
            'detail': log.detail,
            'created_at': log.created_at,
        }
        for log, account in rows
    ], total


def _local_day_range(day: date) -> tuple[datetime, datetime]:
    '''把本地自然日换算成库里的 UTC naive 区间（created_at 都按 UTC 存）。'''
    local_tz = datetime.now().astimezone().tzinfo
    start_local = datetime.combine(day, time.min, tzinfo=local_tz)
    end_local = start_local + timedelta(days=1)
    start = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start, end


def report(db: Session, enterprise_id: int, day: date) -> dict:
    start, end = _local_day_range(day)
    rows = db.execute(
        select(
            EnterpriseImage.uploader_account_id,
            EnterpriseImage.review_status,
            func.count(EnterpriseImage.id),
        )
        .join(Image, Image.id == EnterpriseImage.image_id)
        .where(
            EnterpriseImage.enterprise_id == enterprise_id,
            EnterpriseImage.created_at >= start,
            EnterpriseImage.created_at < end,
            Image.deleted_at.is_(None),
        )
        .group_by(EnterpriseImage.uploader_account_id, EnterpriseImage.review_status)
    ).all()

    per_member: dict[int, dict[str, int]] = {}
    for account_id, status, count in rows:
        bucket = per_member.setdefault(
            account_id, {'uploads': 0, 'approved': 0, 'rejected': 0, 'pending': 0}
        )
        bucket['uploads'] += int(count)
        if status in ('approved', 'rejected', 'pending'):
            bucket[status] += int(count)

    account_ids = list(per_member)
    usernames = (
        dict(
            db.execute(
                select(Account.id, Account.username).where(Account.id.in_(account_ids))
            ).all()
        )
        if account_ids
        else {}
    )

    by_member = [
        {'username': usernames.get(account_id, f'用户 {account_id}'), **bucket}
        for account_id, bucket in per_member.items()
    ]
    by_member.sort(key=lambda item: (-item['uploads'], item['username']))

    uploads = sum(item['uploads'] for item in by_member)
    approved = sum(item['approved'] for item in by_member)
    rejected = sum(item['rejected'] for item in by_member)
    pending = sum(item['pending'] for item in by_member)
    decided = approved + rejected
    return {
        'date': day,
        'uploads': uploads,
        'approved': approved,
        'rejected': rejected,
        'pending': pending,
        'pass_rate': round(approved * 100 / decided, 1) if decided else None,
        'active_members': len(by_member),
        'by_member': by_member,
    }
