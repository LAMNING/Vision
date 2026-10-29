from pydantic import Field

from app.schemas.base import InputModel, ORMModel


class StorageInfoRead(ORMModel):
    '''当前生效的存储后端信息（只读）。'''

    uri: str | None
    backend: str
    supported_schemes: list[str]


class StorageEntryRead(ORMModel):
    '''存储里的一个条目（文件或目录）。'''

    path: str
    name: str
    is_dir: bool
    size: int = 0
    human_size: str
    mtime: float | None = None
    etag: str | None = None
    storage: str


class StorageUsageRead(ORMModel):
    path: str
    file_count: int
    dir_count: int
    total_bytes: int


class StoragePathRequest(InputModel):
    path: str = Field(min_length=1, max_length=1024)


class StorageMoveRequest(InputModel):
    src: str = Field(min_length=1, max_length=1024)
    dst: str = Field(min_length=1, max_length=1024)


class StorageSignedUrlRead(ORMModel):
    url: str
    expires_in: int
