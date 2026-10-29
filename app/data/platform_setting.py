from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import BasicModel
from app.utils.time import utcnow

DEFAULT_TEAM_QUOTA_IMAGES = 10_000
DEFAULT_TEAM_QUOTA_STORAGE_MB = 10_240


class PlatformSetting(BasicModel):
    __tablename__ = 'platform_setting'

    id: Mapped[int] = mapped_column(primary_key=True, default=1)

    storage_uri: Mapped[str | None] = mapped_column(String(255), nullable=True)
    team_quota_images: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_TEAM_QUOTA_IMAGES
    )
    team_quota_storage_mb: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_TEAM_QUOTA_STORAGE_MB
    )

    detector_onnx: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detector_dir: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detector_precision: Mapped[str | None] = mapped_column(String(8), nullable=True)
    detector_device: Mapped[str | None] = mapped_column(String(16), nullable=True)
    detector_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detector_max_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    translator_dir: Mapped[str | None] = mapped_column(String(255), nullable=True)
    translator_precision: Mapped[str | None] = mapped_column(String(8), nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, onupdate=utcnow
    )
    updated_by_account_id: Mapped[int | None] = mapped_column(
        ForeignKey('account.id', ondelete='SET NULL'), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f'<PlatformSetting storage_uri={self.storage_uri!r} '
            f'team_quota_images={self.team_quota_images}>'
        )
