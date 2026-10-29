import mimetypes

from fastapi import APIRouter, Response

from app.data.crud import audit as audit_crud
from app.deps import DBSession, PlatformAdmin
from app.errors import ConflictError, StoragePathNotFoundError, ValidationError
from app.schemas.storage import (
    StorageEntryRead,
    StorageInfoRead,
    StorageMoveRequest,
    StoragePathRequest,
    StorageSignedUrlRead,
    StorageUsageRead,
)
from app.services import storage
from app.services.unifile import FileExists, FileInfo, FileNotFound, UnsupportedOperation

router = APIRouter(prefix='/storage', tags=['storage'])


def _check_path(path: str | None) -> str:
    value = (path or '').strip()
    if '\x00' in value:
        raise ValidationError('存储路径不能包含空字符')
    if '\\' in value:
        raise ValidationError('存储路径请使用 /，不要用 \\')
    parts = [part for part in value.lstrip('/').split('/') if part]
    if any(part == '..' for part in parts):
        raise ValidationError('存储路径不能包含 ..')
    if any(part == '.' for part in parts):
        raise ValidationError('存储路径不能包含 .')
    return '/'.join(parts)


def _store():
    return storage.get_storage()


def _entry(info: FileInfo) -> StorageEntryRead:
    return StorageEntryRead(
        path=info.path,
        name=info.name,
        is_dir=info.is_dir,
        size=info.size,
        human_size=info.human_size,
        mtime=info.mtime or None,
        etag=info.etag,
        storage=info.storage,
    )


def _media_type(path: str) -> str:
    return mimetypes.guess_type(path)[0] or 'application/octet-stream'


@router.get('/info', response_model=StorageInfoRead)
def storage_info(account: PlatformAdmin):
    '''当前生效的后端：平台存储策略 > 环境变量 > 默认本地目录。'''
    uri = storage.effective_uri()
    return StorageInfoRead(
        uri=uri,
        backend=storage.backend_label(uri),
        supported_schemes=sorted(storage.SUPPORTED_SCHEMES),
    )


@router.get('/entries', response_model=list[StorageEntryRead])
def list_entries(account: PlatformAdmin, path: str = '', recursive: bool = False):
    path = _check_path(path)
    try:
        infos = _store().list(path, recursive=recursive)
    except FileNotFound:
        raise StoragePathNotFoundError(path) from None
    return [_entry(info) for info in infos]


@router.get('/stat', response_model=StorageEntryRead)
def stat_path(path: str, account: PlatformAdmin):
    path = _check_path(path)
    try:
        info = _store().stat(path)
    except FileNotFound:
        raise StoragePathNotFoundError(path) from None
    return _entry(info)


@router.get('/download')
def download_path(path: str, account: PlatformAdmin):
    '''取任意对象的原始字节（排查用）。文件名在 Content-Disposition 里。'''
    path = _check_path(path)
    if not path:
        raise ValidationError('path 不能为空')
    try:
        data = _store().read_bytes(path)
    except FileNotFound:
        raise StoragePathNotFoundError(path) from None
    name = path.rsplit('/', 1)[-1]
    return Response(
        content=data,
        media_type=_media_type(path),
        headers={'Content-Disposition': f'inline; filename="{name}"'},
    )


@router.get('/usage', response_model=StorageUsageRead)
def usage(account: PlatformAdmin, path: str = ''):
    '''递归统计某目录下的文件数/目录数/总字节。'''
    path = _check_path(path)
    try:
        infos = _store().list(path, recursive=True)
    except FileNotFound:
        raise StoragePathNotFoundError(path) from None
    file_count = sum(1 for info in infos if not info.is_dir)
    dir_count = sum(1 for info in infos if info.is_dir)
    total_bytes = sum(info.size for info in infos if not info.is_dir)
    return StorageUsageRead(
        path=path,
        file_count=file_count,
        dir_count=dir_count,
        total_bytes=total_bytes,
    )


@router.post('/directories', response_model=StorageEntryRead, status_code=201)
def create_directory(payload: StoragePathRequest, account: PlatformAdmin, db: DBSession):
    path = _check_path(payload.path)
    if not path:
        raise ValidationError('path 不能为空')
    try:
        info = _store().mkdir(path)
    except FileNotFound:
        raise StoragePathNotFoundError(path) from None
    audit_crud.record(
        db,
        action='storage.mkdir',
        resource_type='storage',
        resource_id=0,
        actor_account_id=account.id,
        after={'path': path},
        commit=True,
    )
    return _entry(info)


@router.post('/move', response_model=StorageEntryRead)
def move_path(payload: StorageMoveRequest, account: PlatformAdmin, db: DBSession):
    src = _check_path(payload.src)
    dst = _check_path(payload.dst)
    if not src or not dst:
        raise ValidationError('src/dst 不能为空')
    try:
        info = _store().move(src, dst)
    except FileNotFound:
        raise StoragePathNotFoundError(src) from None
    except FileExists:
        raise ConflictError(f'storage path {dst!r} already exists') from None
    audit_crud.record(
        db,
        action='storage.moved',
        resource_type='storage',
        resource_id=0,
        actor_account_id=account.id,
        after={'src': src, 'dst': dst},
        commit=True,
    )
    return _entry(info)


@router.delete('/entries', status_code=204)
def delete_path(path: str, account: PlatformAdmin, db: DBSession, recursive: bool = False):
    '''删除文件/目录；目录必须显式 recursive=true。'''
    path = _check_path(path)
    if not path:
        raise ValidationError('不能删除存储根目录')
    try:
        _store().delete(path, recursive=recursive)
    except FileNotFound:
        raise StoragePathNotFoundError(path) from None
    except UnsupportedOperation as exc:
        raise ValidationError(f'删除失败：{exc}') from None
    audit_crud.record(
        db,
        action='storage.deleted',
        resource_type='storage',
        resource_id=0,
        actor_account_id=account.id,
        after={'path': path, 'recursive': recursive},
        commit=True,
    )
    return Response(status_code=204)


@router.get('/signed-url', response_model=StorageSignedUrlRead)
def signed_url(path: str, account: PlatformAdmin, expires: int = 3600):
    '''对象存储（OSS）的预签名 URL；本地等后端会返回 400。'''
    path = _check_path(path)
    if not path:
        raise ValidationError('path 不能为空')
    store = _store()
    signer = getattr(store, 'signed_url', None)
    if signer is None:
        raise ValidationError('当前存储后端不支持预签名 URL')
    expires = max(60, min(expires, 7 * 24 * 3600))
    try:
        url = signer(path, expires)
    except UnsupportedOperation:
        raise ValidationError('当前存储后端不支持预签名 URL') from None
    return StorageSignedUrlRead(url=url, expires_in=expires)
