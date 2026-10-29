'''企业空间：成员、加入申请、图库审核、配额、公告、日报与在线状态。'''

from datetime import date
from typing import Annotated

from fastapi import APIRouter, File, Query, UploadFile, status

from app.data import Account, Enterprise
from app.data.crud import enterprise as enterprise_crud
from app.data.enterprise import ROLE_RANK
from app.deps import CurrentAccount, DBSession
from app.errors import (
    EnterpriseRoleError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.schemas.base import Page
from app.schemas.enterprise import (
    EnterpriseCreate,
    EnterpriseHeartbeat,
    EnterpriseHeartbeatRead,
    EnterpriseImageRead,
    EnterpriseJoinRequestCreate,
    EnterpriseJoinRequestRead,
    EnterpriseJoinResult,
    EnterpriseListItem,
    EnterpriseLogRead,
    EnterpriseMemberCreate,
    EnterpriseMemberRead,
    EnterpriseMemberUpdate,
    EnterpriseRankRead,
    EnterpriseReportRead,
    EnterpriseReviewRequest,
    EnterpriseUpdate,
    EnterpriseUsageRead,
)
from app.services.image_upload import (
    PreparedImage,
    discard_prepared,
    persist_image,
    prepare_image,
    validate_upload_size,
)
from app.utils.time import utcnow

router = APIRouter(prefix='/enterprises', tags=['enterprises'])

MAX_BATCH_FILES = 20
DEFAULT_IMAGE_LIMIT = 50
MAX_IMAGE_LIMIT = 200
DEFAULT_LOG_LIMIT = 50
MAX_LOG_LIMIT = 200
REVIEW_QUEUE_LIMIT = 200


# ---- 辅助 ----

def _item(
    db,
    enterprise: Enterprise,
    account_id: int,
    role: str | None = None,
) -> EnterpriseListItem:
    '''列表项与详情共用：统计 + 我在这个企业里的角色，前端一次拿全。'''
    if role is None:
        member = enterprise_crud.get_membership(db, enterprise.id, account_id)
        role = member.role if member is not None else None
    image_count, used_bytes, review_pending = enterprise_crud.image_stats(
        db, enterprise.id
    )
    return EnterpriseListItem(
        id=enterprise.id,
        name=enterprise.name,
        code=enterprise.code,
        email=enterprise.email,
        owner_account_id=enterprise.owner_account_id,
        quota_images=enterprise.quota_images,
        quota_members=enterprise.quota_members,
        auto_approve_staff=enterprise.auto_approve_staff,
        join_mode=enterprise.join_mode,
        notice_text=enterprise.notice_text,
        created_at=enterprise.created_at,
        my_role=role,
        group_id=enterprise.group_id,
        member_count=enterprise_crud.count_members(db, enterprise.id),
        image_count=image_count,
        used_bytes=used_bytes,
        review_pending=review_pending,
        pending_count=enterprise_crud.count_pending_requests(db, enterprise.id),
    )


def _member_view(member, upload_count: int) -> EnterpriseMemberRead:
    return EnterpriseMemberRead(
        id=member.id,
        account_id=member.account_id,
        role=member.role,
        invisible=member.invisible,
        online=enterprise_crud.online_of(member),
        upload_count=upload_count,
        joined_at=member.joined_at,
        account=member.account,
    )


def _admin(db, enterprise_id: int, account: Account):
    return enterprise_crud.require_membership(
        db, enterprise_id, account.id, minimum='admin'
    )


# ---- 企业 ----

@router.get('', response_model=list[EnterpriseListItem])
def list_enterprises(
    account: CurrentAccount, db: DBSession
) -> list[EnterpriseListItem]:
    '''我加入的所有企业，按加入顺序。'''
    rows = enterprise_crud.list_my_enterprises(db, account.id)
    return [_item(db, enterprise, account.id, role) for enterprise, role in rows]


@router.post(
    '', response_model=EnterpriseListItem, status_code=status.HTTP_201_CREATED
)
def create_enterprise(
    payload: EnterpriseCreate, account: CurrentAccount, db: DBSession
) -> EnterpriseListItem:
    '''创建企业；创建者直接是 owner，企业码由服务端生成。'''
    enterprise = enterprise_crud.create_enterprise(
        db,
        owner=account,
        name=payload.name,
        email=payload.email,
        quota_images=payload.quota_images,
        quota_members=payload.quota_members,
    )
    return _item(db, enterprise, account.id, 'owner')


@router.post('/join', response_model=EnterpriseJoinResult)
def join_enterprise(
    payload: EnterpriseJoinRequestCreate, account: CurrentAccount, db: DBSession
) -> EnterpriseJoinResult:
    '''凭企业码加入：open 模式直接成为成员，approval 模式进入待审批队列。'''
    enterprise, result = enterprise_crud.join_enterprise(
        db, code=payload.code, account=account, message=payload.message
    )
    return EnterpriseJoinResult(
        status=result, enterprise=_item(db, enterprise, account.id)
    )


@router.get('/{enterprise_id}', response_model=EnterpriseListItem)
def get_enterprise(
    enterprise_id: int, account: CurrentAccount, db: DBSession
) -> EnterpriseListItem:
    enterprise, member = enterprise_crud.require_membership(
        db, enterprise_id, account.id
    )
    return _item(db, enterprise, account.id, member.role)


@router.patch('/{enterprise_id}', response_model=EnterpriseListItem)
def update_enterprise(
    enterprise_id: int,
    payload: EnterpriseUpdate,
    account: CurrentAccount,
    db: DBSession,
) -> EnterpriseListItem:
    '''企业级设置（名称 / 配额 / 审核规则 / 公告）只有 owner 能改。'''
    enterprise, _member = enterprise_crud.require_membership(
        db, enterprise_id, account.id, minimum='owner'
    )
    enterprise = enterprise_crud.update_enterprise(
        db,
        enterprise,
        account.id,
        **payload.model_dump(exclude_unset=True),
    )
    return _item(db, enterprise, account.id, 'owner')


@router.delete('/{enterprise_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_enterprise(
    enterprise_id: int, account: CurrentAccount, db: DBSession
) -> None:
    '''解散企业（仅 owner）：成员、企业图与 blob、审核记录一并清掉。'''
    enterprise, _member = enterprise_crud.require_membership(
        db, enterprise_id, account.id, minimum='owner'
    )
    enterprise_crud.delete_enterprise(db, enterprise)


# ---- 加入申请 ----

@router.get(
    '/{enterprise_id}/requests', response_model=list[EnterpriseJoinRequestRead]
)
def list_join_requests(
    enterprise_id: int, account: CurrentAccount, db: DBSession
) -> list[EnterpriseJoinRequestRead]:
    _admin(db, enterprise_id, account)
    return [
        EnterpriseJoinRequestRead(
            account_id=row.account_id,
            message=row.message,
            applied_at=row.applied_at,
            account=row.account,
        )
        for row in enterprise_crud.list_requests(db, enterprise_id)
    ]


@router.post(
    '/{enterprise_id}/requests/{account_id}/approve',
    response_model=EnterpriseJoinRequestRead,
)
def approve_join_request(
    enterprise_id: int,
    account_id: int,
    account: CurrentAccount,
    db: DBSession,
) -> EnterpriseJoinRequestRead:
    enterprise, _actor = _admin(db, enterprise_id, account)
    row = enterprise_crud.decide_request(
        db,
        enterprise,
        account_id=account_id,
        decision='approve',
        actor_account_id=account.id,
    )
    return EnterpriseJoinRequestRead(
        account_id=row.account_id,
        message=row.message,
        applied_at=row.applied_at,
        account=row.account,
    )


@router.post(
    '/{enterprise_id}/requests/{account_id}/reject',
    response_model=EnterpriseJoinRequestRead,
)
def reject_join_request(
    enterprise_id: int,
    account_id: int,
    account: CurrentAccount,
    db: DBSession,
) -> EnterpriseJoinRequestRead:
    enterprise, _actor = _admin(db, enterprise_id, account)
    row = enterprise_crud.decide_request(
        db,
        enterprise,
        account_id=account_id,
        decision='reject',
        actor_account_id=account.id,
    )
    return EnterpriseJoinRequestRead(
        account_id=row.account_id,
        message=row.message,
        applied_at=row.applied_at,
        account=row.account,
    )


# ---- 成员 ----

@router.get('/{enterprise_id}/members', response_model=list[EnterpriseMemberRead])
def list_members(
    enterprise_id: int, account: CurrentAccount, db: DBSession
) -> list[EnterpriseMemberRead]:
    enterprise_crud.require_membership(db, enterprise_id, account.id)
    return [
        _member_view(member, count)
        for member, count in enterprise_crud.list_members(db, enterprise_id)
    ]


@router.post(
    '/{enterprise_id}/members',
    response_model=EnterpriseMemberRead,
    status_code=status.HTTP_201_CREATED,
)
def invite_member(
    enterprise_id: int,
    payload: EnterpriseMemberCreate,
    account: CurrentAccount,
    db: DBSession,
) -> EnterpriseMemberRead:
    '''按用户名直接邀请；管理员只能邀请不高于自己的角色。'''
    enterprise, actor = _admin(db, enterprise_id, account)
    if actor.role != 'owner' and ROLE_RANK[payload.role] >= ROLE_RANK[actor.role]:
        raise PermissionDeniedError('不能授予不低于自己的角色')
    member = enterprise_crud.invite_member(
        db,
        enterprise,
        username=payload.username,
        role=payload.role,
        actor_account_id=account.id,
    )
    return _member_view(member, 0)


@router.patch(
    '/{enterprise_id}/members/{account_id}', response_model=EnterpriseMemberRead
)
def update_member(
    enterprise_id: int,
    account_id: int,
    payload: EnterpriseMemberUpdate,
    account: CurrentAccount,
    db: DBSession,
) -> EnterpriseMemberRead:
    '''本人只能改隐身；改角色需要管理员及以上。'''
    enterprise, actor = enterprise_crud.require_membership(
        db, enterprise_id, account.id
    )
    if payload.role is not None and ROLE_RANK[actor.role] < ROLE_RANK['admin']:
        raise EnterpriseRoleError('管理员')
    member = enterprise_crud.update_member(
        db,
        enterprise,
        actor=actor,
        target_account_id=account_id,
        role=payload.role,
        invisible=payload.invisible,
    )
    upload_count = dict(
        (m.account_id, count)
        for m, count in enterprise_crud.list_members(db, enterprise_id)
    ).get(member.account_id, 0)
    return _member_view(member, upload_count)


@router.delete(
    '/{enterprise_id}/members/{account_id}', status_code=status.HTTP_204_NO_CONTENT
)
def remove_member(
    enterprise_id: int,
    account_id: int,
    account: CurrentAccount,
    db: DBSession,
) -> None:
    enterprise, actor = _admin(db, enterprise_id, account)
    if account_id == account.id:
        raise PermissionDeniedError('退出企业请使用退出接口')
    enterprise_crud.remove_member(
        db, enterprise, actor=actor, target_account_id=account_id
    )


@router.post(
    '/{enterprise_id}/leave', status_code=status.HTTP_204_NO_CONTENT
)
def leave_enterprise(
    enterprise_id: int, account: CurrentAccount, db: DBSession
) -> None:
    '''退出企业；owner 不能退出（409），需要先解散或转让。'''
    enterprise, member = enterprise_crud.require_membership(
        db, enterprise_id, account.id
    )
    enterprise_crud.leave_enterprise(db, enterprise, member)


# ---- 图库 ----

@router.get('/{enterprise_id}/images', response_model=Page[EnterpriseImageRead])
def list_images(
    enterprise_id: int,
    account: CurrentAccount,
    db: DBSession,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_IMAGE_LIMIT)] = DEFAULT_IMAGE_LIMIT,
) -> Page[EnterpriseImageRead]:
    enterprise_crud.require_membership(db, enterprise_id, account.id)
    items, total = enterprise_crud.list_images(
        db, enterprise_id, offset=offset, limit=limit
    )
    return Page[EnterpriseImageRead](
        items=[EnterpriseImageRead.model_validate(item) for item in items],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post(
    '/{enterprise_id}/images',
    response_model=list[EnterpriseImageRead],
    status_code=status.HTTP_201_CREATED,
)
def upload_images(
    enterprise_id: int,
    account: CurrentAccount,
    db: DBSession,
    files: list[UploadFile] = File(...),
) -> list[EnterpriseImageRead]:
    '''一次传 1~20 张（multipart 字段名 ``files``）。

    图片落在企业背后的隐藏组里（去重按企业算），再挂一条 ``EnterpriseImage``：
    开了 auto_approve_staff 时管理员及以上上传的直接 approved，其余进待审。
    '''
    enterprise, member = enterprise_crud.require_membership(
        db, enterprise_id, account.id
    )
    if member.role == 'guest':
        raise PermissionDeniedError('只读访客不能上传图片')
    if not files:
        raise ValidationError('没有选择文件')
    if len(files) > MAX_BATCH_FILES:
        raise ValidationError(f'一次最多上传 {MAX_BATCH_FILES} 张图片')

    payloads: list[tuple[str, bytes]] = []
    for upload in files:
        data = upload.file.read()
        filename = (upload.filename or 'image').strip() or 'image'
        validate_upload_size(data)
        payloads.append((filename, data))

    enterprise_crud.assert_image_quota(db, enterprise, len(payloads))

    review_status = enterprise_crud.initial_review_status(enterprise, member.role)
    prepared: list[PreparedImage] = []
    links = []
    try:
        for filename, data in payloads:
            prepared.append(
                prepare_image(
                    db,
                    group_id=enterprise.group_id,
                    data=data,
                    filename=filename,
                )
            )

        for item in prepared:
            image, _created = persist_image(
                db,
                item,
                group_id=enterprise.group_id,
                uploader_account_id=account.id,
                require_group_member=False,
                commit=False,
            )
            link, _linked = enterprise_crud.link_image(
                db,
                enterprise_id=enterprise.id,
                image_id=image.id,
                uploader_account_id=account.id,
                review_status=review_status,
                commit=False,
            )
            links.append((link, image))
        enterprise_crud.record_log(
            db,
            enterprise.id,
            account.id,
            'image.uploaded',
            f'上传了 {len(links)} 张图片',
        )
        db.commit()
    except Exception:
        db.rollback()
        for item in prepared:
            discard_prepared(item)
        raise

    return [
        EnterpriseImageRead.model_validate(enterprise_crud.image_payload(link, image))
        for link, image in links
    ]


@router.delete(
    '/{enterprise_id}/images/{image_id}',
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_image(
    enterprise_id: int,
    image_id: int,
    account: CurrentAccount,
    db: DBSession,
) -> None:
    '''管理员及以上，或图片上传者本人。'''
    enterprise, member = enterprise_crud.require_membership(
        db, enterprise_id, account.id
    )
    link = enterprise_crud.get_image_link(db, enterprise_id, image_id)
    if link is None:
        raise NotFoundError('这张图片不在企业图库里')
    if member.role not in ('owner', 'admin') and link.uploader_account_id != account.id:
        raise PermissionDeniedError('只有管理员或上传者本人可以删除企业图片')
    enterprise_crud.delete_image(
        db, enterprise, image_id=image_id, actor_account_id=account.id
    )


@router.get('/{enterprise_id}/review', response_model=list[EnterpriseImageRead])
def list_review_queue(
    enterprise_id: int, account: CurrentAccount, db: DBSession
) -> list[EnterpriseImageRead]:
    enterprise_crud.require_membership(
        db, enterprise_id, account.id, minimum='reviewer'
    )
    items, _total = enterprise_crud.list_images(
        db, enterprise_id, limit=REVIEW_QUEUE_LIMIT, review_status='pending'
    )
    return [EnterpriseImageRead.model_validate(item) for item in items]


@router.post(
    '/{enterprise_id}/images/{image_id}/review',
    response_model=EnterpriseImageRead,
)
def review_image(
    enterprise_id: int,
    image_id: int,
    payload: EnterpriseReviewRequest,
    account: CurrentAccount,
    db: DBSession,
) -> EnterpriseImageRead:
    enterprise, _member = enterprise_crud.require_membership(
        db, enterprise_id, account.id, minimum='reviewer'
    )
    item = enterprise_crud.review_image(
        db,
        enterprise,
        image_id=image_id,
        decision=payload.decision,
        note=payload.note,
        reviewer_account_id=account.id,
    )
    return EnterpriseImageRead.model_validate(item)


@router.get('/{enterprise_id}/usage', response_model=EnterpriseUsageRead)
def get_usage(
    enterprise_id: int, account: CurrentAccount, db: DBSession
) -> EnterpriseUsageRead:
    enterprise, _member = enterprise_crud.require_membership(
        db, enterprise_id, account.id
    )
    image_count, used_bytes, _pending = enterprise_crud.image_stats(db, enterprise.id)
    return EnterpriseUsageRead(
        image_count=image_count,
        used_bytes=used_bytes,
        quota_images=enterprise.quota_images,
        member_count=enterprise_crud.count_members(db, enterprise.id),
        quota_members=enterprise.quota_members,
    )


@router.get('/{enterprise_id}/ranking', response_model=list[EnterpriseRankRead])
def get_ranking(
    enterprise_id: int, account: CurrentAccount, db: DBSession
) -> list[EnterpriseRankRead]:
    enterprise_crud.require_membership(db, enterprise_id, account.id)
    return [
        EnterpriseRankRead.model_validate(row)
        for row in enterprise_crud.ranking(db, enterprise_id)
    ]


# ---- 日志 / 日报 / 心跳 ----

@router.get('/{enterprise_id}/logs', response_model=Page[EnterpriseLogRead])
def list_logs(
    enterprise_id: int,
    account: CurrentAccount,
    db: DBSession,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_LOG_LIMIT)] = DEFAULT_LOG_LIMIT,
) -> Page[EnterpriseLogRead]:
    enterprise_crud.require_membership(db, enterprise_id, account.id)
    items, total = enterprise_crud.list_logs(
        db, enterprise_id, offset=offset, limit=limit
    )
    return Page[EnterpriseLogRead](
        items=[EnterpriseLogRead.model_validate(item) for item in items],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get('/{enterprise_id}/report', response_model=EnterpriseReportRead)
def get_report(
    enterprise_id: int,
    account: CurrentAccount,
    db: DBSession,
    day: Annotated[date | None, Query(alias='date')] = None,
) -> EnterpriseReportRead:
    '''默认统计今天（服务器本地时区）。'''
    enterprise_crud.require_membership(db, enterprise_id, account.id)
    payload = enterprise_crud.report(db, enterprise_id, day or date.today())
    return EnterpriseReportRead.model_validate(payload)


@router.post('/{enterprise_id}/heartbeat', response_model=EnterpriseHeartbeatRead)
def heartbeat(
    enterprise_id: int,
    payload: EnterpriseHeartbeat,
    account: CurrentAccount,
    db: DBSession,
) -> EnterpriseHeartbeatRead:
    '''前端每 20 秒打一次；在线状态由 last_seen_at 现算，不落库。'''
    _enterprise, member = enterprise_crud.require_membership(
        db, enterprise_id, account.id
    )
    member = enterprise_crud.heartbeat(db, member, invisible=payload.invisible)
    return EnterpriseHeartbeatRead(
        online=enterprise_crud.online_of(member),
        invisible=member.invisible,
        server_time=utcnow(),
    )
