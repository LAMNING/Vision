import json
from typing import Annotated

from fastapi import APIRouter, File, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.data import Account, Group, Image, Project
from app.data.crud import audit as audit_crud
from app.data.crud import image as image_crud
from app.data.crud import project as project_crud
from app.data.crud import project_image as project_image_crud
from app.data.crud import project_member as project_member_crud
from app.data.crud.annotation import export_coco, list_project_annotations
from app.data.crud.group import get_group
from app.data.crud.project import list_viewable_projects
from app.data.crud.project_access import (
    ProjectAccess,
    require_manage,
    require_upload,
    require_view,
    resolve_project_access,
)
from app.data.crud.project_auto_config import get_or_create_config, update_config
from app.data.crud.project_stats import project_stats as project_stats_crud
from app.data.crud.project_category import (
    create_category,
    delete_category,
    get_category_or_raise,
    list_categories,
    update_category,
)
from app.data.crud.review import REVIEWER_ROLES
from app.deps import CurrentAccount, DBSession
from app.errors import (
    CategoryNotFoundError,
    PermissionDeniedError,
    ProjectImageNotFoundError,
    ProjectMemberNotFoundError,
    ValidationError,
)
from app.schemas.annotation import AnnotationListItem, AnnotationRead
from app.schemas.base import Page
from app.schemas.image import ImageRead
from app.schemas.project import ProjectListItem, ProjectRead, ProjectUpdate
from app.schemas.project_auto_config import AutoConfigRead, AutoConfigUpdate
from app.schemas.project_category import (
    CategoryCreate,
    CategoryRead,
    CategoryUpdate,
)
from app.schemas.project_member import (
    ProjectMemberCreate,
    ProjectMemberRead,
    ProjectMemberRolesUpdate,
)
from app.schemas.project_stats import ProjectStatsRead
from app.services.image_upload import (
    PreparedImage,
    discard_prepared,
    persist_image,
    prepare_image,
    validate_upload_size,
)

router = APIRouter(prefix='/projects', tags=['projects'])

DEFAULT_ANNOTATION_LIMIT = 50
MAX_ANNOTATION_LIMIT = 200
DEFAULT_IMAGE_LIMIT = 50
MAX_IMAGE_LIMIT = 200
MAX_BATCH_FILES = 20


# ---- 辅助 ----

def _project_item(db: Session, account: Account, project: Project) -> ProjectListItem:
    access = resolve_project_access(db, account, project.id)
    group = db.get(Group, project.group_id)
    can_review = access.is_personal_owner or bool(
        set(access.roles) & set(REVIEWER_ROLES)
    )
    return ProjectListItem(
        **{
            'id': project.id,
            'group_id': project.group_id,
            'name': project.name,
            'description': project.description,
            'default_reviewer': project.default_reviewer,
            'status': project.status,
            'created_at': project.created_at,
            'group_name': group.name if group is not None else '',
            'group_type': group.type if group is not None else '',
            'role': access.label,
            'can_review': can_review,
        }
    )


def _accessible(db: Session, account: Account, project_id: int) -> ProjectAccess:
    return require_view(db, account, project_id)


def _managed(db: Session, account: Account, project_id: int) -> ProjectAccess:
    return require_manage(db, account, project_id)


def _category_in_project(
    db: Session, project_id: int, category_id: int
) -> None:
    '''类目必须属于路径里的项目，否则按「类目不存在」处理。'''
    category = get_category_or_raise(db, category_id)
    if category.project_id != project_id:
        raise CategoryNotFoundError(category_id)


# ---- 项目 ----

@router.get('', response_model=list[ProjectListItem])
def list_projects(
    account: CurrentAccount,
    db: DBSession,
    include_archived: Annotated[bool, Query()] = False,
) -> list[ProjectListItem]:
    '''我参与的所有项目（个人空间 + 团队 + 被邀请加入的）。'''
    return [
        _project_item(db, account, project)
        for project in list_viewable_projects(
            db, account.id, include_archived=include_archived
        )
    ]


@router.get('/{project_id}', response_model=ProjectListItem)
def get_project(
    project_id: int, account: CurrentAccount, db: DBSession
) -> ProjectListItem:
    access = _accessible(db, account, project_id)
    return _project_item(db, account, access.project)


@router.patch('/{project_id}', response_model=ProjectRead)
def update_project(
    project_id: int,
    payload: ProjectUpdate,
    account: CurrentAccount,
    db: DBSession,
) -> ProjectRead:
    '''改项目名/描述/默认评审人（项目 manager 或空间 owner/admin）。'''
    _managed(db, account, project_id)
    fields = payload.model_dump(exclude_unset=True)
    project = project_crud.update_project(db, project_id, **fields)
    audit_crud.record(
        db,
        action='project.updated',
        resource_type='project',
        resource_id=project_id,
        actor_account_id=account.id,
        after=fields,
        commit=True,
    )
    return ProjectRead.model_validate(project)


@router.post('/{project_id}/archive', response_model=ProjectRead)
def archive_project(
    project_id: int, account: CurrentAccount, db: DBSession
) -> ProjectRead:
    '''归档项目（归档后默认列表不再显示，数据保留）。'''
    _managed(db, account, project_id)
    project = project_crud.archive_project(db, project_id)
    audit_crud.record(
        db,
        action='project.archived',
        resource_type='project',
        resource_id=project_id,
        actor_account_id=account.id,
        after={'status': 'archived'},
        commit=True,
    )
    return ProjectRead.model_validate(project)


@router.post('/{project_id}/restore', response_model=ProjectRead)
def restore_project(
    project_id: int, account: CurrentAccount, db: DBSession
) -> ProjectRead:
    '''把归档项目恢复成 active。'''
    _managed(db, account, project_id)
    project = project_crud.restore_project(db, project_id)
    audit_crud.record(
        db,
        action='project.restored',
        resource_type='project',
        resource_id=project_id,
        actor_account_id=account.id,
        after={'status': 'active'},
        commit=True,
    )
    return ProjectRead.model_validate(project)


@router.delete('/{project_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: int, account: CurrentAccount, db: DBSession) -> None:
    '''软删除项目（项目下的图片/任务记录保留，但项目不可再访问）。'''
    _managed(db, account, project_id)
    project_crud.delete_project(db, project_id)
    audit_crud.record(
        db,
        action='project.deleted',
        resource_type='project',
        resource_id=project_id,
        actor_account_id=account.id,
        commit=True,
    )


@router.get('/{project_id}/members', response_model=list[ProjectMemberRead])
def list_project_members(
    project_id: int, account: CurrentAccount, db: DBSession
) -> list[ProjectMemberRead]:
    '''项目成员（含账号摘要），任务指派的下拉框用它。'''
    require_view(db, account, project_id)
    return [
        ProjectMemberRead.model_validate(row)
        for row in project_member_crud.list_members(db, project_id)
    ]


def _active_manager_accounts(db: Session, project_id: int) -> set[int]:
    return {
        row.account_id
        for row in project_member_crud.list_members(db, project_id)
        if 'manager' in set(row.roles or [])
    }


def _ensure_manager_remains(
    db: Session, project_id: int, *, exclude_account_id: int | None = None
) -> None:
    '''移除/降级前确认项目里仍至少有一个 active manager。'''
    managers = _active_manager_accounts(db, project_id)
    if exclude_account_id is not None:
        managers.discard(exclude_account_id)
    if not managers:
        raise ValidationError('项目至少要保留一个 manager，不能移除或降级最后一个 manager')


@router.post(
    '/{project_id}/members',
    response_model=ProjectMemberRead,
    status_code=status.HTTP_201_CREATED,
)
def add_project_member(
    project_id: int,
    payload: ProjectMemberCreate,
    account: CurrentAccount,
    db: DBSession,
) -> ProjectMemberRead:
    '''直接往项目里加成员/追加角色（manager 权限）。'''
    require_manage(db, account, project_id)
    if db.get(Account, payload.account_id) is None:
        raise ValidationError(f'account {payload.account_id} not found')
    row = project_member_crud.upsert_member(
        db,
        project_id,
        payload.account_id,
        role=payload.role,
        expires_at=payload.expires_at,
        source_group_id=payload.source_group_id,
    )
    db.commit()
    db.refresh(row)
    audit_crud.record(
        db,
        action='project.member_added',
        resource_type='project_member',
        resource_id=row.id,
        actor_account_id=account.id,
        after={
            'project_id': project_id,
            'account_id': payload.account_id,
            'roles': list(row.roles or []),
        },
        commit=True,
    )
    return ProjectMemberRead.model_validate(row)


@router.patch('/{project_id}/members/{account_id}', response_model=ProjectMemberRead)
def update_project_member(
    project_id: int,
    account_id: int,
    payload: ProjectMemberRolesUpdate,
    account: CurrentAccount,
    db: DBSession,
) -> ProjectMemberRead:
    '''整组覆盖成员角色（manager 权限）。'''
    require_manage(db, account, project_id)
    membership = project_member_crud.get_membership(db, project_id, account_id)
    if membership is None or membership.status != 'active':
        raise ProjectMemberNotFoundError(project_id, account_id)
    if 'manager' not in payload.roles and 'manager' in set(membership.roles or []):
        _ensure_manager_remains(db, project_id, exclude_account_id=account_id)
    row = project_member_crud.change_roles(
        db, project_id, account_id, roles=list(payload.roles)
    )
    audit_crud.record(
        db,
        action='project.member_roles_changed',
        resource_type='project_member',
        resource_id=row.id,
        actor_account_id=account.id,
        after={'roles': list(row.roles or [])},
        commit=True,
    )
    return ProjectMemberRead.model_validate(row)


@router.delete(
    '/{project_id}/members/{account_id}', status_code=status.HTTP_204_NO_CONTENT
)
def remove_project_member(
    project_id: int,
    account_id: int,
    account: CurrentAccount,
    db: DBSession,
) -> None:
    '''移除项目成员（manager 权限；不能移除最后一个 manager）。'''
    require_manage(db, account, project_id)
    membership = project_member_crud.get_membership(db, project_id, account_id)
    if membership is None or membership.status != 'active':
        raise ProjectMemberNotFoundError(project_id, account_id)
    if 'manager' in set(membership.roles or []):
        _ensure_manager_remains(db, project_id, exclude_account_id=account_id)
    project_member_crud.remove_member(db, project_id, account_id)
    audit_crud.record(
        db,
        action='project.member_removed',
        resource_type='project_member',
        resource_id=membership.id,
        actor_account_id=account.id,
        after={'project_id': project_id, 'account_id': account_id},
        commit=True,
    )


@router.get('/{project_id}/stats', response_model=ProjectStatsRead)
def get_project_stats(
    project_id: int, account: CurrentAccount, db: DBSession
) -> ProjectStatsRead:
    '''项目统计：任务状态/模式分布、图片数、标注（来源/类别/天）、复核通过率。'''
    require_view(db, account, project_id)
    return ProjectStatsRead.model_validate(project_stats_crud(db, project_id))


# ---- 项目图片 ----


@router.post(
    '/{project_id}/images',
    response_model=list[ImageRead],
    status_code=status.HTTP_201_CREATED,
)
def upload_project_images(
    project_id: int,
    account: CurrentAccount,
    db: DBSession,
    files: list[UploadFile] = File(...),
) -> list[Image]:
    '''一次往项目里传多张图（1~20 张，multipart 字段名 ``files``）。

    图片仍落在项目所属的组里（额度、去重按组算），再挂到项目上；
    同组里已有同 sha256 的图会复用记录，不会重复存 blob。
    '''
    access = require_upload(db, account, project_id)

    if not files:
        raise ValidationError('没有选择文件')
    if len(files) > MAX_BATCH_FILES:
        raise ValidationError(f'一次最多上传 {MAX_BATCH_FILES} 张图片')

    # 先把整批校验完，再动存储和数据库：半途出错不会留下垃圾 blob
    payloads: list[tuple[str, bytes]] = []
    for upload in files:
        data = upload.file.read()
        filename = (upload.filename or 'image').strip() or 'image'
        validate_upload_size(data)
        payloads.append((filename, data))

    prepared: list[PreparedImage] = []
    images: list[Image] = []
    try:
        # 第一阶段只写存储（缩略图生成是慢 I/O），不碰数据库事务；
        # 第二阶段用一个短事务把整批写进库并立刻提交。
        # 两个阶段分开是为了不长时间占住 SQLite 写锁（见 app/database.py 注释）。
        for filename, data in payloads:
            prepared.append(
                prepare_image(
                    db,
                    group_id=access.project.group_id,
                    data=data,
                    filename=filename,
                )
            )

        for item in prepared:
            image, created = persist_image(
                db,
                item,
                group_id=access.project.group_id,
                uploader_account_id=account.id,
                require_group_member=False,
                commit=False,
            )

            linked = project_image_crud.link_image(
                db,
                project_id=project_id,
                image_id=image.id,
                added_by_account_id=account.id,
                commit=False,
            )
            if linked:
                audit_crud.record(
                    db,
                    action='project.image_added',
                    resource_type='image',
                    resource_id=image.id,
                    actor_account_id=account.id,
                    group_id=access.project.group_id,
                    after={
                        'project_id': project_id,
                        'filename': item.filename,
                        'deduplicated': not created,
                    },
                    commit=False,
                )
            images.append(image)
        db.commit()
    except Exception:
        db.rollback()
        for item in prepared:
            discard_prepared(item)
        raise

    return images


@router.get('/{project_id}/images', response_model=Page[ImageRead])
def list_project_images(
    project_id: int,
    account: CurrentAccount,
    db: DBSession,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_IMAGE_LIMIT)] = DEFAULT_IMAGE_LIMIT,
) -> Page[ImageRead]:
    '''项目里已有的图片（软删的不算）。'''
    require_view(db, account, project_id)
    rows, total = project_image_crud.list_project_images(
        db, project_id, offset=offset, limit=limit
    )
    return Page[ImageRead](
        items=[ImageRead.model_validate(row) for row in rows],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.delete(
    '/{project_id}/images/{image_id}', status_code=status.HTTP_204_NO_CONTENT
)
def remove_project_image(
    project_id: int, image_id: int, account: CurrentAccount, db: DBSession
) -> Response:
    '''把图片从项目里移除（只删关联，图片本身还在组里）。'''
    access = require_view(db, account, project_id)
    image = image_crud.get_image_or_raise(db, image_id)

    if image.group_id != access.project.group_id:
        raise ProjectImageNotFoundError(project_id, image_id)
    if not access.can_upload and image.uploader_account_id != account.id:
        raise PermissionDeniedError(
            '只有项目 manager/annotator 或上传者可以从项目移除图片'
        )

    if not project_image_crud.remove_project_image(
        db, project_id, image_id, commit=False
    ):
        raise ProjectImageNotFoundError(project_id, image_id)

    audit_crud.record(
        db,
        action='project.image_removed',
        resource_type='image',
        resource_id=image_id,
        actor_account_id=account.id,
        group_id=image.group_id,
        before={'project_id': project_id, 'filename': image.filename},
        commit=False,
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- 类目 ----

@router.get('/{project_id}/categories', response_model=list[CategoryRead])
def list_project_categories(
    project_id: int,
    account: CurrentAccount,
    db: DBSession,
    include_disabled: Annotated[bool, Query()] = False,
) -> list:
    _accessible(db, account, project_id)
    return list_categories(db, project_id, include_disabled=include_disabled)


@router.post(
    '/{project_id}/categories',
    response_model=CategoryRead,
    status_code=status.HTTP_201_CREATED,
)
def create_project_category(
    payload: CategoryCreate,
    project_id: int,
    account: CurrentAccount,
    db: DBSession,
) -> object:
    _managed(db, account, project_id)
    return create_category(
        db, project_id=project_id, **payload.model_dump()
    )


@router.patch(
    '/{project_id}/categories/{category_id}', response_model=CategoryRead
)
def update_project_category(
    payload: CategoryUpdate,
    project_id: int,
    category_id: int,
    account: CurrentAccount,
    db: DBSession,
) -> object:
    _managed(db, account, project_id)
    _category_in_project(db, project_id, category_id)
    return update_category(
        db, category_id, **payload.model_dump(exclude_unset=True)
    )


@router.delete(
    '/{project_id}/categories/{category_id}',
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_project_category(
    project_id: int,
    category_id: int,
    account: CurrentAccount,
    db: DBSession,
) -> None:
    _managed(db, account, project_id)
    _category_in_project(db, project_id, category_id)
    delete_category(db, category_id)


# ---- 自动标注配置 ----

@router.get('/{project_id}/auto-config', response_model=AutoConfigRead)
def get_project_auto_config(
    project_id: int, account: CurrentAccount, db: DBSession
) -> object:
    _accessible(db, account, project_id)
    return get_or_create_config(db, project_id)


@router.patch('/{project_id}/auto-config', response_model=AutoConfigRead)
def update_project_auto_config(
    payload: AutoConfigUpdate,
    project_id: int,
    account: CurrentAccount,
    db: DBSession,
) -> object:
    _managed(db, account, project_id)
    return update_config(
        db, project_id, **payload.model_dump(exclude_unset=True)
    )


# ---- 标注列表 ----

@router.get(
    '/{project_id}/annotations', response_model=Page[AnnotationListItem]
)
def list_project_annotation_items(
    project_id: int,
    account: CurrentAccount,
    db: DBSession,
    task_id: Annotated[int | None, Query(gt=0)] = None,
    task_image_id: Annotated[int | None, Query(gt=0)] = None,
    category_id: Annotated[int | None, Query(gt=0)] = None,
    is_current: Annotated[bool | None, Query()] = True,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_ANNOTATION_LIMIT)] = (
        DEFAULT_ANNOTATION_LIMIT
    ),
) -> Page[AnnotationListItem]:
    _accessible(db, account, project_id)

    annotations, total = list_project_annotations(
        db,
        project_id,
        task_id=task_id,
        task_image_id=task_image_id,
        category_id=category_id,
        is_current=is_current,
        offset=offset,
        limit=limit,
    )

    items: list[AnnotationListItem] = []
    for ann in annotations:
        row = ann.task_image
        image = row.image if row is not None else None
        items.append(
            AnnotationListItem(
                **AnnotationRead.model_validate(ann).model_dump(),
                task_id=row.task_id if row is not None else None,
                image_id=image.id if image is not None else None,
                image_filename=image.filename if image is not None else None,
            )
        )

    return Page[AnnotationListItem](
        items=items, total=total, offset=offset, limit=limit
    )


# ---- 标注导出 ----


@router.get('/{project_id}/annotations/export')
def export_project_annotations(
    project_id: int,
    account: CurrentAccount,
    db: DBSession,
    fmt: Annotated[str, Query(alias='format')] = 'coco',
) -> Response:
    '''导出项目当前（is_current）的 bbox 标注，默认 COCO JSON。'''
    require_view(db, account, project_id)

    normalized = (fmt or '').strip().lower()
    if normalized != 'coco':
        raise ValidationError('目前只支持 format=coco')

    payload = export_coco(db, project_id)
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
    return Response(
        content=body,
        media_type='application/json',
        headers={
            'Content-Disposition': (
                f'attachment; filename="project-{project_id}-coco.json"'
            )
        },
    )

