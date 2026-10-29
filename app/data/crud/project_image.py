from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.data.image import Image
from app.data.project_image import ProjectImage
from app.data.project_member import ProjectMember


def get_link(db: Session, project_id: int, image_id: int) -> ProjectImage | None:
    return db.get(ProjectImage, (project_id, image_id))


def link_image(
    db: Session,
    *,
    project_id: int,
    image_id: int,
    added_by_account_id: int | None,
    commit: bool = True,
) -> bool:
    if get_link(db, project_id, image_id) is not None:
        return False

    db.add(
        ProjectImage(
            project_id=project_id,
            image_id=image_id,
            added_by_account_id=added_by_account_id,
        )
    )
    if commit:
        db.commit()
    else:
        db.flush()
    return True


def remove_project_image(
    db: Session, project_id: int, image_id: int, *, commit: bool = True
) -> bool:
    link = get_link(db, project_id, image_id)
    if link is None:
        return False
    db.delete(link)
    if commit:
        db.commit()
    else:
        db.flush()
    return True


def _active_condition():
    return Image.deleted_at.is_(None)


def count_project_images(db: Session, project_id: int) -> int:
    stmt = (
        select(func.count(Image.id))
        .join(ProjectImage, ProjectImage.image_id == Image.id)
        .where(ProjectImage.project_id == project_id, _active_condition())
    )
    return int(db.scalar(stmt) or 0)


def list_project_images(
    db: Session, project_id: int, *, offset: int = 0, limit: int = 50
) -> tuple[list[Image], int]:
    stmt = (
        select(Image)
        .join(ProjectImage, ProjectImage.image_id == Image.id)
        .where(ProjectImage.project_id == project_id, _active_condition())
        .order_by(Image.id)
        .offset(offset)
        .limit(limit)
    )
    rows = list(db.scalars(stmt).all())
    return rows, count_project_images(db, project_id)


def account_can_access_via_project(
    db: Session, image_id: int, account_id: int
) -> bool:
    '''不是组员、但被加进了某个包含这张图的项目 → 也能看。'''
    stmt = (
        select(ProjectMember.project_id)
        .join(ProjectImage, ProjectImage.project_id == ProjectMember.project_id)
        .where(
            ProjectImage.image_id == image_id,
            ProjectMember.account_id == account_id,
            ProjectMember.status == 'active',
        )
        .limit(1)
    )
    return db.scalar(stmt) is not None
