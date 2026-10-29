from typing import Annotated

from fastapi import APIRouter, Query, status
from sqlalchemy.orm import Session

from app.data import Account, Group, Project
from app.data.crud.group import create_personal_group, get_personal_group
from app.data.crud.project import (
    archive_project,
    create_project,
    delete_project,
    get_project_or_raise,
    list_group_projects,
    restore_project,
    update_project,
)
from app.deps import CurrentAccount, DBSession
from app.errors import ProjectNotFoundError, ValidationError
from app.schemas.project import ProjectCreate, ProjectRead, ProjectUpdate

router = APIRouter(prefix='/space', tags=['space'])


def _my_space(db: Session, account: Account) -> Group:
    return get_personal_group(db, account.id) or create_personal_group(
        db, account=account
    )


def _my_project(db: Session, account: Account, project_id: int) -> Project:
    project = get_project_or_raise(db, project_id)
    space = get_personal_group(db, account.id)
    if space is None or project.group_id != space.id:
        raise ProjectNotFoundError(project_id)

    return project


@router.get('/projects', response_model=list[ProjectRead])
def list_space_projects(
    account: CurrentAccount,
    db: DBSession,
    include_archived: Annotated[bool, Query()] = False,
) -> list[Project]:
    space = get_personal_group(db, account.id)
    if space is None:
        return []

    return list_group_projects(db, space.id, include_archived=include_archived)


@router.post(
    '/projects',
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
)
def create_space_project(
    payload: ProjectCreate, account: CurrentAccount, db: DBSession
) -> Project:
    space = _my_space(db, account)

    if payload.manager_account_id not in (None, account.id):
        raise ValidationError('个人空间里的项目只能由空间主人管理')

    return create_project(
        db,
        group_id=space.id,
        name=payload.name,
        description=payload.description,
        default_reviewer=payload.default_reviewer,
        manager_account_id=account.id,
    )


@router.get('/projects/{project_id}', response_model=ProjectRead)
def get_space_project(
    project_id: int, account: CurrentAccount, db: DBSession
) -> Project:
    return _my_project(db, account, project_id)


@router.patch('/projects/{project_id}', response_model=ProjectRead)
def update_space_project(
    payload: ProjectUpdate,
    project_id: int,
    account: CurrentAccount,
    db: DBSession,
) -> Project:
    _my_project(db, account, project_id)
    return update_project(db, project_id, **payload.model_dump(exclude_unset=True))


@router.post('/projects/{project_id}/archive', response_model=ProjectRead)
def archive_space_project(
    project_id: int, account: CurrentAccount, db: DBSession
) -> Project:
    _my_project(db, account, project_id)
    return archive_project(db, project_id)


@router.post('/projects/{project_id}/restore', response_model=ProjectRead)
def restore_space_project(
    project_id: int, account: CurrentAccount, db: DBSession
) -> Project:
    _my_project(db, account, project_id)
    return restore_project(db, project_id)


@router.delete(
    '/projects/{project_id}', status_code=status.HTTP_204_NO_CONTENT
)
def delete_space_project(
    project_id: int, account: CurrentAccount, db: DBSession
) -> None:
    _my_project(db, account, project_id)
    delete_project(db, project_id)
