'''平台管理员专用模型：请求体 + 管理端只读视图。'''

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.base import InputModel, Password
from app.services import storage as storage_service

__all__ = [
    'PlatformRoleUpdate',
    'PasswordResetRequest',
    'AdminStatsRead',
    'AdminGroupRead',
    'AdminProjectRead',
    'StoragePolicyUpdate',
    'StoragePolicyRead',
    'ModelConfigUpdate',
    'ModelConfigRead',
]


class PlatformRoleUpdate(InputModel):
    '''只允许 user / admin 两种平台角色；改自己会被 400 拒掉。'''

    platform_role: Literal['user', 'admin']


class PasswordResetRequest(InputModel):
    '''管理员重置密码：改完 token_version +1，目标账号所有旧 token 立即失效。'''

    new_password: Password


class AdminStatsRead(BaseModel):
    accounts: int
    admins: int
    groups: int
    projects: int
    images: int
    tasks: int
    annotations: int
    pending_invitations: int


class AdminGroupRead(BaseModel):
    id: int
    name: str
    type: str
    owner_account_id: int
    owner_username: str | None
    member_count: int
    created_at: datetime
    deleted_at: datetime | None


class AdminProjectRead(BaseModel):
    id: int
    group_id: int
    group_name: str | None
    name: str
    status: str
    member_count: int
    task_count: int
    created_at: datetime
    deleted_at: datetime | None


class StoragePolicyUpdate(InputModel):
    '''平台存储策略（PATCH 语义）：只改传了的字段。

    - `storage_uri`：存储后端 URI；传 `null` 表示清空（回到环境变量/默认值）。
    - `team_quota_images` / `team_quota_storage_mb`：新建 team 组的默认额度，0=不限。
    '''

    storage_uri: str | None = Field(default=None, max_length=255)
    team_quota_images: int | None = Field(default=None, ge=0)
    team_quota_storage_mb: int | None = Field(default=None, ge=0)

    @field_validator('storage_uri')
    @classmethod
    def _check_storage_uri(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return storage_service.validate_storage_uri(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class StoragePolicyRead(BaseModel):
    storage_uri: str | None
    team_quota_images: int
    team_quota_storage_mb: int
    effective_uri: str
    backend: str
    updated_at: datetime | None


class ModelConfigUpdate(InputModel):
    '''平台模型配置（PATCH 语义）：传 null / 空串表示回到环境变量。

    - `detector_onnx`：显式 onnx 文件，优先于 `detector_dir`。
    - `detector_dir`：onnx 目录，按 `detector_precision` 选文件名。
    - `translator_dir`：Marian（opus-mt-zh-en）目录。
    '''

    detector_onnx: str | None = Field(default=None, max_length=255)
    detector_dir: str | None = Field(default=None, max_length=255)
    detector_precision: Literal['fp32', 'fp16'] | None = None
    detector_device: Literal['cpu', 'cuda', 'auto'] | None = None
    detector_size: int | None = Field(default=None, ge=64, le=4096)
    detector_max_size: int | None = Field(default=None, ge=64, le=4096)
    translator_dir: str | None = Field(default=None, max_length=255)
    translator_precision: Literal['fp32', 'fp16'] | None = None


class ModelConfigRead(BaseModel):
    '''DB 里保存的模型配置 + 当前实际生效值（DB > 环境变量 > 默认）。'''

    detector_onnx: str | None
    detector_dir: str | None
    detector_precision: str | None
    detector_device: str | None
    detector_size: int | None
    detector_max_size: int | None
    translator_dir: str | None
    translator_precision: str | None

    effective_detector_path: str | None
    effective_detector_precision: str | None
    effective_detector_device: str | None
    effective_detector_size: int
    effective_detector_max_size: int
    effective_translator_dir: str | None
    effective_translator_precision: str

    detector_configured: bool
    translator_configured: bool
    detector_loaded: bool
    translator_loaded: bool
    detector_path_exists: bool = False
    translator_dir_exists: bool = False
    updated_at: datetime | None
