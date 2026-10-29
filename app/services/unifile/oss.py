"""对象存储（OSS / S3 兼容）后端。

只依赖标准库：``urllib`` 发请求，``hmac``/``hashlib`` 做 SigV4 签名，
``xml.etree.ElementTree`` 解析 ListObjects 响应。

兼容范围：
- 阿里云 OSS（S3 兼容接口，``endpoint`` 传 ``oss-cn-hangzhou.aliyuncs.com``）
- AWS S3
- MinIO、腾讯云 COS 等 S3 兼容服务

签名支持两种方式：
- ``s3v4``：AWS Signature Version 4（默认，S3 兼容服务通用）
- ``oss-hmac-sha1``：阿里云 OSS 原生 V1 签名

鉴权支持 AK/SK、STS 临时凭证、匿名访问，以及从环境变量读取。
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import hmac
import os
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any, Iterator

from ._http import ConnectionError_, HttpError, make_transport
from ._resume import ResumeState, ResumeStore
from ._util import (
    ensure_trailing_slash,
    guess_region,
    norm_path,
    split_prefix,
    uri_encode,
)
from .base import (
    FileInfo,
    FileNotFound,
    NotAFile,
    Storage,
    StorageError,
    _run_jobs,
)

_S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


def _canonical_uri(path: str) -> str:
    """把 URL 路径规范成 SigV4 的 CanonicalURI。

    关键在**先 unquote 再 quote**：``_url()`` 生成的路径已经是编码过的，
    直接 ``quote()`` 会把 ``%E4`` 变成 ``%25E4``（双重编码），导致中文、
    空格这类 key 的签名全部失败——英文 key 不受影响，所以很容易漏测。

    先还原成原始 key 再编码一次，正好得到「单次编码」的正确形式；
    对本身就含 ``%`` 的 key 也成立（``%20`` 字面量在 URL 里是 ``%2520``，
    还原一次得到 ``%20``，再编码回去还是 ``%2520``）。

    Args:
        path: URL 里的路径部分（已百分号编码）。

    Returns:
        用于签名的 canonical URI。
    """
    return urllib.parse.quote(urllib.parse.unquote(path or "/"), safe="/-_.~")


def _safe_mtime(path: str) -> float:
    """读取文件修改时间，失败返回 0（0 表示"不参与校验"）。"""
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


class OssError(StorageError):
    """对象存储返回的业务错误。"""


class _NoSuchUpload(Exception):
    """分片上传任务已不存在（过期或被 Abort）。

    内部异常：调用方据此丢弃续传存档、改为全新上传。
    """


class OssStorage(Storage):
    """对象存储后端（S3 协议）。

    Example:
        >>> store = OssStorage(
        ...     bucket="my-bucket",
        ...     endpoint="oss-cn-hangzhou.aliyuncs.com",
        ...     access_key_id="...",
        ...     access_key_secret="...",
        ... )
        >>> store.upload("photo.jpg", "images/photo.jpg")
    """

    kind = "oss"

    def __init__(
        self,
        bucket: str,
        *,
        endpoint: str = "",
        access_key_id: str | None = None,
        access_key_secret: str | None = None,
        security_token: str | None = None,
        region: str | None = None,
        prefix: str = "",
        signature_version: str = "s3v4",
        addressing_style: str = "virtual",
        verify_ssl: bool = True,
        timeout: float = 30.0,
        max_keys: int = 1000,
        max_retries: int = 3,
        multipart_threshold: int = 8 * 1024 * 1024,
        part_size: int = 8 * 1024 * 1024,
        multipart_concurrency: int = 4,
        resume: bool = True,
        resume_dir: str | None = None,
        transport: str = "stdlib",
        proxy: str | None = None,
        no_proxy: str | None = None,
    ) -> None:
        """
        Args:
            bucket: 桶名。
            endpoint: 服务域名，如 ``oss-cn-hangzhou.aliyuncs.com``。
                留空则按 region 推断 AWS 端点。
            access_key_id: 访问密钥 ID；留空则读环境变量
                ``OSS_ACCESS_KEY_ID`` / ``AWS_ACCESS_KEY_ID``。
            access_key_secret: 访问密钥；留空则读环境变量
                ``OSS_ACCESS_KEY_SECRET`` / ``AWS_SECRET_ACCESS_KEY``。
            security_token: STS 临时令牌；留空则读 ``OSS_SECURITY_TOKEN``。
            region: 区域；留空则从 endpoint 推断。
            prefix: 所有操作相对于该 key 前缀。
            signature_version: 签名算法。

                - ``s3v4``：标准 AWS SigV4
                - ``cosv4``：腾讯云 COS 变体（待签字符串里时间戳留空），
                  用 ``cos://`` 时默认选它
                - ``oss-hmac-sha1``：阿里云 OSS 原生 V1 签名
            addressing_style: ``virtual``（``bucket.endpoint``）或
                ``path``（``endpoint/bucket``）。
            verify_ssl: 是否校验证书。
            timeout: 单次请求超时（秒）。
            max_keys: 列举时每页请求的对象数。
            max_retries: 请求失败时的重试次数。
            multipart_threshold: 超过该大小自动走分片上传（字节）。
            part_size: 分片大小（字节），S3 要求不小于 5MB。
            multipart_concurrency: 分片上传的并发数。每片独立建连接，
                峰值内存约为「并发数 × 分片大小」。
            resume: 是否启用跨进程断点续传。开启后分片上传失败会保留
                服务端任务与本地存档，下次上传同一文件自动接着传。
            resume_dir: 续传存档目录。留空用平台默认位置，也可用
                环境变量 ``UNIFILE_RESUME_DIR`` 指定。
            transport: HTTP 传输层，``stdlib``（默认，零依赖）或
                ``requests``（需装 ``unifile[requests]``）。
                OSS 签名对 URL 编码敏感，两种实现都已适配，可按需选择。
            proxy: 代理地址（``requests`` 走会话配置，``stdlib`` 走隧道）。
            no_proxy: 不走代理的主机列表。
        """
        super().__init__(prefix)
        if not bucket:
            raise ValueError("bucket 不能为空")
        self.bucket = bucket
        self.access_key_id = (
            access_key_id
            or os.environ.get("OSS_ACCESS_KEY_ID")
            or os.environ.get("AWS_ACCESS_KEY_ID")
            or ""
        )
        self.access_key_secret = (
            access_key_secret
            or os.environ.get("OSS_ACCESS_KEY_SECRET")
            or os.environ.get("AWS_SECRET_ACCESS_KEY")
            or ""
        )
        self.security_token = (
            security_token or os.environ.get("OSS_SECURITY_TOKEN") or ""
        )
        self.region = region or guess_region(endpoint) if endpoint else (region or "us-east-1")
        if not endpoint:
            endpoint = f"s3.{self.region}.amazonaws.com"
        # 记录原始 scheme（http:// 前缀用于本地测试与私有部署），再剥掉
        self._use_http = endpoint.lower().startswith("http://")
        self.endpoint = endpoint.replace("https://", "").replace("http://", "").strip("/")
        self.signature_version = signature_version
        self.addressing_style = addressing_style
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.max_keys = max_keys
        self.max_retries = max_retries
        self.multipart_threshold = multipart_threshold
        self.part_size = max(part_size, 5 * 1024 * 1024)
        self.multipart_concurrency = max(1, multipart_concurrency)
        self.resume = resume
        self._resume = ResumeStore(resume_dir)
        self._prefix = ensure_trailing_slash(prefix.replace("\\", "/").strip("/"))
        self._pool = make_transport(
            transport,
            timeout=timeout,
            verify_ssl=verify_ssl,
            max_retries=max_retries,
            proxy=proxy,
            no_proxy=no_proxy,
        )

    # ------------------------------------------------------------------
    # URL 构造
    # ------------------------------------------------------------------
    def _host(self) -> str:
        if self.addressing_style == "path":
            return self.endpoint
        return f"{self.bucket}.{self.endpoint}"

    def _base_path(self) -> str:
        if self.addressing_style == "path":
            return f"/{self.bucket}"
        return ""

    def _scheme(self) -> str:
        """请求协议。endpoint 里显式写了 http:// 就用 http。"""
        return "http" if self._use_http else "https"

    def _key_of(self, path: str) -> str:
        """把相对路径映射为完整的 object key。"""
        rel = norm_path(path)
        if not self._prefix:
            return rel
        if not rel:
            return self._prefix.rstrip("/")
        return self._prefix + rel

    def _rel_of(self, key: str) -> str:
        """把 object key 映射回相对路径。"""
        if not self._prefix:
            return norm_path(key)
        if key == self._prefix.rstrip("/"):
            return ""
        return norm_path(split_prefix(key, self._prefix))

    def _url(self, path: str, params: dict[str, str] | None = None) -> str:
        """构造请求 URL。

        Args:
            path: 相对路径；空串表示桶根（用于 ListObjects）。
            params: 查询参数。值为空串的参数按 ``key=`` 形式输出
                （S3 的 ``?uploads`` 子资源需要这种形式）。
        """
        key = self._key_of(path)
        quoted = "/".join(uri_encode(part) for part in key.split("/") if part)
        url = f"{self._scheme()}://{self._host()}{self._base_path()}"
        if quoted:
            url += "/" + quoted
        if params:
            url += "?" + self._encode_query(params)
        return url

    @staticmethod
    def _encode_query(params: dict[str, str]) -> str:
        """编码查询串。保持参数顺序，``/`` 与 ``,`` 不转义。"""
        return "&".join(
            f"{uri_encode(k)}={uri_encode(v)}" for k, v in params.items()
        )

    def _list_url(self, params: dict[str, str]) -> str:
        """ListObjects 的 URL：path-style 下请求桶本身，不带 key 前缀。"""
        url = f"{self._scheme()}://{self._host()}{self._base_path()}"
        if params:
            url += "?" + self._encode_query(params)
        return url

    # ------------------------------------------------------------------
    # 签名
    # ------------------------------------------------------------------
    def _sign_v4(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        payload_hash: str,
    ) -> dict[str, str]:
        """AWS Signature Version 4 签名。"""
        parsed = urllib.parse.urlsplit(url)
        canonical_uri = _canonical_uri(parsed.path)
        query_pairs = sorted(
            urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        )
        canonical_query = "&".join(
            f"{uri_encode(k)}={uri_encode(v)}" for k, v in query_pairs
        )

        signed_headers_map = {k.lower(): str(v).strip() for k, v in headers.items()}
        signed_headers_map["host"] = parsed.netloc
        signed_header_names = ";".join(sorted(signed_headers_map))
        canonical_headers = "".join(
            f"{name}:{signed_headers_map[name]}\n" for name in sorted(signed_headers_map)
        )

        canonical_request = "\n".join(
            [
                method,
                canonical_uri,
                canonical_query,
                canonical_headers,
                signed_header_names,
                payload_hash,
            ]
        )

        now = _dt.datetime.now(_dt.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        scope = f"{date_stamp}/{self.region}/s3/aws4_request"

        # 腾讯云 COS 的 S3 兼容接口在 StringToSign 的第二行（时间戳）留空，
        # 与 AWS 规范不同。这是实测出来的：标准 SigV4 被拒 403，留空则通过；
        # 用错误密钥或篡改签名仍然 403，说明它确实在逐位校验。
        # 用 signature_version="cosv4" 切换。注意 X-Amz-Date 头照常发送，
        # 只是不参与待签字符串。
        sts_date = "" if self.signature_version == "cosv4" else amz_date
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                sts_date,
                scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )

        key = ("AWS4" + self.access_key_secret).encode("utf-8")
        for part in (date_stamp, self.region, "s3", "aws4_request"):
            key = hmac.new(key, part.encode("utf-8"), hashlib.sha256).digest()
        signature = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        out = dict(headers)
        out["X-Amz-Date"] = amz_date
        out["X-Amz-Content-Sha256"] = payload_hash
        if self.security_token:
            out["X-Amz-Security-Token"] = self.security_token
        out["Authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self.access_key_id}/{scope}, "
            f"SignedHeaders={signed_header_names}, Signature={signature}"
        )
        return out

    def _sign_oss_v1(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        payload_hash: str,
    ) -> dict[str, str]:
        """阿里云 OSS 原生 V1 签名（HMAC-SHA1）。"""
        parsed = urllib.parse.urlsplit(url)
        now = _dt.datetime.now(_dt.timezone.utc)
        date_str = now.strftime("%a, %d %b %Y %H:%M:%S GMT")
        content_md5 = headers.get("Content-MD5", "")
        content_type = headers.get("Content-Type", "")

        oss_headers = {k.lower(): v for k, v in headers.items() if k.lower().startswith("x-oss-")}
        canonical_oss_headers = "".join(
            f"{k}:{oss_headers[k]}\n" for k in sorted(oss_headers)
        )
        resource = parsed.path
        if self.addressing_style == "path":
            resource = f"/{self.bucket}{parsed.path}"
        sub_resource = ""
        if parsed.query:
            sub_resource = "?" + parsed.query
        canonical_resource = resource + sub_resource

        string_to_sign = "\n".join(
            [method, content_md5, content_type, date_str, canonical_oss_headers, canonical_resource]
        )
        digest = hmac.new(
            self.access_key_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        signature = __import__("base64").b64encode(digest).decode("ascii")

        out = dict(headers)
        out["Date"] = date_str
        out["Authorization"] = f"OSS {self.access_key_id}:{signature}"
        if self.security_token:
            out["x-oss-security-token"] = self.security_token
        return out

    def _prepare(
        self, method: str, url: str, headers: dict[str, str], body: bytes
    ) -> dict[str, str]:
        if not self.access_key_id or not self.access_key_secret:
            return dict(headers)  # 匿名访问
        payload_hash = hashlib.sha256(body or b"").hexdigest()
        if self.signature_version == "oss-hmac-sha1":
            return self._sign_oss_v1(method, url, headers, payload_hash)
        return self._sign_v4(method, url, headers, payload_hash)

    def _signer(self, method: str, url: str, headers: dict[str, str], payload_hash: str) -> dict[str, str]:
        """给传输层用的签名回调。"""
        if not self.access_key_id or not self.access_key_secret:
            return dict(headers)
        if self.signature_version == "oss-hmac-sha1":
            return self._sign_oss_v1(method, url, headers, payload_hash)
        return self._sign_v4(method, url, headers, payload_hash)

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------
    def _request(
        self,
        method: str,
        url: str,
        *,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        stream: bool = False,
        body_file: Any = None,
        body_length: int | None = None,
        payload_hash: str | None = None,
        compress: bool = False,
    ) -> Any:
        """发送请求，返回响应体 bytes 或原始响应对象。

        Args:
            body: 字节请求体。
            body_file: 文件对象请求体（流式上传用）。
            body_length: ``body_file`` 的字节数。
            payload_hash: 签名用的 payload 哈希；流式上传时需显式提供。
        """
        base_headers = dict(headers or {})
        if payload_hash is None:
            payload_hash = hashlib.sha256(body or b"").hexdigest()

        try:
            resp = self._pool.request(
                method,
                url,
                body=body if body is not None else None,
                body_file=body_file,
                body_length=body_length,
                headers=base_headers,
                signer=self._signer,
                payload_hash=payload_hash,
                compress=compress,
            )
        except HttpError as exc:
            if exc.status == 404:
                raise FileNotFound(f"路径不存在（HTTP 404）：{url}") from exc
            raise OssError(str(exc)) from exc
        except ConnectionError_ as exc:
            raise StorageError(f"请求失败：{exc}") from exc

        if stream:
            return resp
        data = resp.read()
        resp.close()
        return data

    # ------------------------------------------------------------------
    # 核心原语
    # ------------------------------------------------------------------
    def _list(self, path: str) -> list[FileInfo]:
        """列举一个"目录"下的直接子项。

        对象存储没有真正的目录，这里用 delimiter="/" 让服务端返回
        CommonPrefixes 作为目录。
        """
        return list(self._iter_list(path))

    def _iter_list(self, path: str) -> Iterator[FileInfo]:
        """流式列举一层子项。

        分页边界由 ``_iter_objects`` 处理，这里逐页产出，不累积整层结果。
        列举大桶时（百万对象）内存占用恒定为单页大小。
        """
        prefix = self._key_of(path)
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        for entry in self._iter_objects(prefix, delimiter="/"):
            if entry["key"] == prefix:
                continue
            yield self._to_info(entry)

    def _iter_objects(
        self, prefix: str, delimiter: str | None = None
    ) -> Iterator[dict[str, Any]]:
        """分页列举对象。"""
        token = ""
        while True:
            params = {
                "list-type": "2",
                "prefix": prefix,
                "max-keys": str(self.max_keys),
            }
            if delimiter:
                params["delimiter"] = delimiter
            if token:
                params["continuation-token"] = token
            url = self._list_url(params)
            xml = self._request("GET", url)
            root = ET.fromstring(xml)

            page: list[dict[str, Any]] = []
            for content in root.findall(f"{_S3_NS}Contents") or root.findall("Contents"):
                key = content.findtext(f"{_S3_NS}Key") or content.findtext("Key") or ""
                size = content.findtext(f"{_S3_NS}Size") or content.findtext("Size") or "0"
                etag = content.findtext(f"{_S3_NS}ETag") or content.findtext("ETag") or ""
                last = (
                    content.findtext(f"{_S3_NS}LastModified")
                    or content.findtext("LastModified")
                    or ""
                )
                page.append(
                    {
                        "key": key,
                        "size": int(size),
                        "etag": etag.strip('"'),
                        "mtime": self._parse_time(last),
                    }
                )
            for common in root.findall(f"{_S3_NS}CommonPrefixes") or root.findall(
                "CommonPrefixes"
            ):
                key = common.findtext(f"{_S3_NS}Prefix") or common.findtext("Prefix") or ""
                page.append({"key": key, "size": 0, "etag": "", "mtime": 0.0, "dir": True})

            yield from page

            truncated = (
                root.findtext(f"{_S3_NS}IsTruncated")
                or root.findtext("IsTruncated")
                or "false"
            ).lower() == "true"
            if not truncated:
                break
            token = (
                root.findtext(f"{_S3_NS}NextContinuationToken")
                or root.findtext("NextContinuationToken")
                or ""
            )
            if not token:
                break

    @staticmethod
    def _parse_time(value: str) -> float:
        if not value:
            return 0.0
        for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                return _dt.datetime.strptime(value, fmt).replace(
                    tzinfo=_dt.timezone.utc
                ).timestamp()
            except ValueError:
                continue
        return 0.0

    def _to_info(self, entry: dict[str, Any]) -> FileInfo:
        key = entry["key"]
        is_dir = bool(entry.get("dir")) or key.endswith("/")
        rel = self._rel_of(key)
        if is_dir:
            rel = rel.rstrip("/")
        return FileInfo(
            path=rel,
            size=0 if is_dir else int(entry.get("size", 0)),
            is_dir=is_dir,
            mtime=float(entry.get("mtime", 0.0)),
            etag=entry.get("etag") or None,
            storage=self.kind,
            extra={"key": key},
        )

    def _stat(self, path: str) -> FileInfo:
        key = self._key_of(path)
        if not key:
            return FileInfo(path="", is_dir=True, storage=self.kind)
        url = self._url(path)
        try:
            resp = self._request("HEAD", url, headers={}, stream=True)
        except FileNotFound:
            # 可能是"目录"，用列举确认
            prefix = key if key.endswith("/") else key + "/"
            for entry in self._iter_objects(prefix, delimiter="/"):
                if entry["key"] == prefix or entry.get("dir"):
                    return FileInfo(
                        path=norm_path(self._rel_of(key)),
                        is_dir=True,
                        storage=self.kind,
                        extra={"key": key},
                    )
            raise
        try:
            size = int(resp.getheader("Content-Length") or 0)
            etag = (resp.getheader("ETag") or "").strip('"')
            last = resp.getheader("Last-Modified") or ""
        finally:
            resp.close()
        return FileInfo(
            path=norm_path(self._rel_of(key)),
            size=size,
            is_dir=False,
            mtime=self._parse_time(last),
            etag=etag or None,
            storage=self.kind,
            extra={"key": key},
        )

    def _read_bytes(self, path: str) -> bytes:
        url = self._url(path)
        return self._request("GET", url)

    def _write_bytes(self, path: str, data: bytes) -> FileInfo:
        key = self._key_of(path)
        if key.endswith("/"):
            raise NotAFile(f"目标以 / 结尾，视为目录：{path}")
        url = self._url(path)
        headers = {"Content-Type": "application/octet-stream"}
        if self.signature_version == "oss-hmac-sha1":
            headers["Content-MD5"] = __import__("base64").b64encode(
                hashlib.md5(data, usedforsecurity=False).digest()
            ).decode("ascii")
            headers["x-oss-meta-source"] = "unifile"
        else:
            headers["x-amz-meta-source"] = "unifile"
        self._request("PUT", url, body=data, headers=headers)
        return FileInfo(
            path=norm_path(self._rel_of(key)),
            size=len(data),
            storage=self.kind,
            extra={"key": key},
        )

    # ------------------------------------------------------------------
    # 流式上传 / 分片上传
    # ------------------------------------------------------------------
    def upload(
        self,
        local_path: str,
        path: str = "",
        *,
        progress: Any = None,
    ) -> FileInfo:
        """上传本地文件（流式，内存占用与文件大小无关）。

        小文件走单次 PUT；超过 ``multipart_threshold`` 自动切换为分片上传，
        支持分片级重试。

        Args:
            local_path: 本地文件路径。
            path: 远端目标路径；留空则用本地文件名。
            progress: 进度回调 ``(done, total)``。
        """
        if not os.path.isfile(local_path):
            raise NotAFile(f"本地文件不存在：{local_path}")
        remote = norm_path(path) or os.path.basename(local_path)
        parent = self._rel_of(self._key_of(remote)).rpartition("/")[0]
        if parent:
            self._makedirs(parent, exist_ok=True)

        total = os.path.getsize(local_path)
        if total <= self.multipart_threshold:
            return self._upload_single(local_path, remote, total, progress)
        return self._upload_multipart(local_path, remote, total, progress)

    def _upload_single(
        self, local_path: str, remote: str, total: int, progress: Any
    ) -> FileInfo:
        """单次 PUT 上传。

        签名需要 payload 的 sha256，这里分块扫描文件计算（不占内存），
        然后再分块发送同一份内容。
        """
        key = self._key_of(remote)
        url = self._url(remote)
        headers = {"Content-Type": "application/octet-stream"}
        if self.signature_version == "oss-hmac-sha1":
            headers["x-oss-meta-source"] = "unifile"
        else:
            headers["x-amz-meta-source"] = "unifile"

        # 第一遍：分块计算 payload 哈希（签名用）
        payload_hash = self._hash_file(local_path, "sha256")
        if self.signature_version == "oss-hmac-sha1":
            headers["Content-MD5"] = self._base64_md5(local_path)

        # 第二遍：分块发送
        done = 0
        with open(local_path, "rb") as fh:
            resp = self._pool.request(
                "PUT",
                url,
                body_file=fh,
                body_length=total,
                headers=headers,
                signer=self._signer,
                payload_hash=payload_hash,
            )
            try:
                resp.read()
            finally:
                resp.close()
        if progress:
            progress(total, total)
        return FileInfo(
            path=norm_path(self._rel_of(key)),
            size=total,
            storage=self.kind,
            extra={"key": key},
        )

    @staticmethod
    def _hash_file(local_path: str, algorithm: str = "sha256") -> str:
        """分块计算文件摘要，不把文件读进内存。"""
        digest = hashlib.new(algorithm)
        with open(local_path, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _base64_md5(local_path: str) -> str:
        """计算文件的 base64(MD5)，用于 OSS V1 签名的 Content-MD5。"""
        digest = hashlib.md5(usedforsecurity=False)
        with open(local_path, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return __import__("base64").b64encode(digest.digest()).decode("ascii")

    def _upload_multipart(
        self, local_path: str, remote: str, total: int, progress: Any
    ) -> FileInfo:
        """分片上传，支持跨进程断点续传。

        分片默认 4 路并发。每个线程用自己的文件句柄 seek 到分片偏移再读，
        所以内存占用是「并发数 × 分片大小」，与文件总大小无关。

        续传的判断依据是服务端的 ``ListParts``，不是本地存档——本地只记
        UploadId。这条路径的开销是一次 ListParts 请求，只在确实存在存档
        时发生。
        """
        key = self._key_of(remote)
        part_size = self._effective_part_size(total)
        part_count = (total + part_size - 1) // part_size

        if not self.resume:
            return self._run_multipart(
                local_path, remote, total, progress,
                part_size=part_size, part_count=part_count,
            )

        fingerprint = self._resume.fingerprint(local_path, key)
        with self._resume.lock(fingerprint) as acquired:
            if not acquired:
                # 另一个进程正在传同一个文件。插一脚只会两边都出错，
                # 所以放弃续传，直接全量重传。
                return self._run_multipart(
                    local_path, remote, total, progress,
                    part_size=part_size, part_count=part_count,
                )

            upload_id, resumed = self._probe_resume(
                local_path, remote, fingerprint, total, part_size
            )
            return self._run_multipart(
                local_path, remote, total, progress,
                part_size=part_size, part_count=part_count,
                upload_id=upload_id, resumed=resumed, fingerprint=fingerprint,
            )

    def _probe_resume(
        self, local_path: str, remote: str, fingerprint: str, total: int, part_size: int
    ) -> tuple[str | None, dict[int, str]]:
        """检查是否存在可续传的上传任务。

        Args:
            local_path: 源文件路径。
            remote: 目标对象路径。
            fingerprint: 存档指纹。
            total: 源文件大小。
            part_size: 本次将使用的分片大小。

        Returns:
            ``(upload_id, 已传分片)``。``upload_id`` 为 ``None`` 表示
            需要新建上传任务。
        """
        state = self._resume.load(fingerprint)
        if state is None:
            return None, {}

        try:
            mtime = os.path.getmtime(local_path)
        except OSError:
            mtime = 0.0

        if not state.matches(total=total, part_size=part_size, mtime=mtime):
            # 源文件变了 / 分片配置变了 / 存档过期：丢弃，重新开始
            self._resume.drop(fingerprint)
            return None, {}

        try:
            parts = self._list_parts(remote, state.upload_id)
        except _NoSuchUpload:
            # 服务端已清理（S3 默认 7 天过期）或别的进程 Abort 了
            self._resume.drop(fingerprint)
            return None, {}
        except Exception:
            # 比如账号没有 ListParts 权限：不影响正确性，只是续传不成
            return None, {}

        if not parts:
            return state.upload_id, {}
        return state.upload_id, parts

    def _run_multipart(
        self,
        local_path: str,
        remote: str,
        total: int,
        progress: Any,
        *,
        part_size: int,
        part_count: int,
        upload_id: str | None = None,
        resumed: dict[int, str] | None = None,
        fingerprint: str | None = None,
    ) -> FileInfo:
        """执行分片上传。

        Args:
            local_path: 源文件。
            remote: 目标对象路径。
            total: 总字节数。
            progress: 进度回调。
            part_size: 分片大小。
            part_count: 分片总数。
            upload_id: 复用的上传任务 ID；``None`` 表示新建。
            resumed: 服务端已收到的分片 ``{分片号: ETag}``。
            fingerprint: 续传存档指纹；``None`` 表示不启用续传。
        """
        key = self._key_of(remote)
        resumed = dict(resumed or {})
        if upload_id is None:
            upload_id = self._create_multipart(remote)

        if fingerprint is not None:
            self._resume.save(
                fingerprint,
                ResumeState(
                    upload_id=upload_id,
                    key=key,
                    total=total,
                    part_size=part_size,
                    mtime=_safe_mtime(local_path),
                    created_at=time.time(),
                ),
            )

        lock = threading.Lock()
        # 已传的分片也要计入进度，否则续传时进度条会从 0 开始跳
        done = sum(self._part_length(n, part_size, total) for n in resumed)
        parts: list[dict[str, Any]] = [
            {"ETag": etag, "PartNumber": number} for number, etag in resumed.items()
        ]

        def upload_one(part_number: int) -> dict[str, Any]:
            nonlocal done
            offset = (part_number - 1) * part_size
            length = min(part_size, total - offset)
            with open(local_path, "rb") as fh:
                fh.seek(offset)
                data = fh.read(length)
            etag = self._upload_part(remote, upload_id, part_number, data)
            if progress:
                with lock:
                    done += length
                    progress(done, total)
            return {"ETag": etag, "PartNumber": part_number}

        todo = [n for n in range(1, part_count + 1) if n not in resumed]
        try:
            if todo:
                parts.extend(_run_jobs(todo, upload_one, self.multipart_concurrency))
            # S3 要求 CompleteMultipartUpload 按 PartNumber 升序提交
            parts.sort(key=lambda item: item["PartNumber"])
            self._complete_multipart(remote, upload_id, parts)
        except _NoSuchUpload:
            # 传到一半任务没了：丢弃存档，清掉残留，全新重传一次
            self._discard_resume(fingerprint)
            with contextlib.suppress(Exception):
                self._abort_multipart(remote, upload_id)
            return self._run_multipart(
                local_path, remote, total, progress,
                part_size=part_size, part_count=part_count,
            )
        except Exception:
            if fingerprint is None:
                # 未启用续传：保持旧语义，失败即清理服务端残留
                with contextlib.suppress(Exception):
                    self._abort_multipart(remote, upload_id)
            # 启用续传时故意不 Abort——任务和分片留着，下次直接接着传
            raise
        else:
            self._discard_resume(fingerprint)

        return FileInfo(
            path=norm_path(self._rel_of(key)),
            size=total,
            storage=self.kind,
            extra={
                "key": key,
                "upload_id": upload_id,
                "parts": len(parts),
                "resumed_parts": len(resumed),
            },
        )

    def _discard_resume(self, fingerprint: str | None) -> None:
        """删除续传存档（成功或确定失效时调用）。"""
        if fingerprint is not None:
            self._resume.drop(fingerprint)

    @staticmethod
    def _part_length(part_number: int, part_size: int, total: int) -> int:
        """第 ``part_number`` 片的字节数（最后一片可能不满）。"""
        offset = (part_number - 1) * part_size
        return max(0, min(part_size, total - offset))

    def abort_stale_uploads(self, prefix: str = "") -> list[str]:
        """清理指定前缀下所有未完成的分片上传。

        进程被 ``kill -9`` 时来不及 Abort，服务端会残留分片并持续计费。
        S3 可以用桶生命周期规则自动清理，本地补一个手动入口。

        只应清理"确实没人要"的任务——正在续传的任务被清掉后，下次会
        退化为全量重传（不会出错，只是白费流量）。

        Args:
            prefix: 只清理该 key 前缀下的任务；留空表示全部。

        Returns:
            被 Abort 的 UploadId 列表。
        """
        aborted: list[str] = []
        marker = ""
        while True:
            params: dict[str, str] = {"uploads": "", "max-uploads": "1000"}
            if prefix:
                params["prefix"] = self._key_of(prefix)
            if marker:
                params["key-marker"] = marker
            url = self._list_url(params)
            data = self._request("GET", url)
            root = ET.fromstring(data)
            for upload in root.findall(f"{_S3_NS}Upload") or root.findall("Upload"):
                key = upload.findtext(f"{_S3_NS}Key") or upload.findtext("Key") or ""
                upload_id = (
                    upload.findtext(f"{_S3_NS}UploadId")
                    or upload.findtext("UploadId")
                    or ""
                )
                if not key or not upload_id:
                    continue
                with contextlib.suppress(Exception):
                    self._abort_multipart(self._rel_of(key), upload_id)
                    aborted.append(upload_id)
            truncated = (
                root.findtext(f"{_S3_NS}IsTruncated")
                or root.findtext("IsTruncated")
                or ""
            ).lower() == "true"
            if not truncated:
                break
            marker = (
                root.findtext(f"{_S3_NS}NextKeyMarker")
                or root.findtext("NextKeyMarker")
                or ""
            )
            if not marker:
                break
        return aborted

    def _effective_part_size(self, total: int) -> int:
        """按 S3 的 10000 片上限定下实际分片大小。

        Args:
            total: 文件总字节数。

        Returns:
            不小于配置值、且能保证分片数不超过 10000 的分片大小。
        """
        base = self.part_size
        if total <= base * 10000:
            return base
        # 向上取整到 1MB，避免出现零头分片
        needed = (total + 9999) // 10000
        return max(base, ((needed + 1024 * 1024 - 1) // (1024 * 1024)) * 1024 * 1024)

    def _create_multipart(self, path: str) -> str:
        """发起分片上传，返回 UploadId。"""
        # S3 接受 "?uploads" 与 "?uploads="，阿里云 OSS 原生要求 "?uploads"
        url = self._url(path).split("?")[0] + "?uploads"
        data = self._request("POST", url)
        root = ET.fromstring(data)
        upload_id = (
            root.findtext(f"{_S3_NS}UploadId")
            or root.findtext("UploadId")
            or ""
        )
        if not upload_id:
            raise OssError("服务端未返回 UploadId，分片上传无法继续")
        return upload_id

    def _list_parts(self, path: str, upload_id: str) -> dict[int, str]:
        """查询某个上传任务已经收到哪些分片。

        这是断点续传的关键：**权威记录在服务端**，本地只存 UploadId。
        进程被杀、状态文件丢失都不会导致已传分片白费。

        Args:
            path: 目标对象路径。
            upload_id: 上传任务 ID。

        Returns:
            ``{分片号: ETag}``。

        Raises:
            _NoSuchUpload: 上传任务已过期或被其他进程 Abort。
        """
        parts: dict[int, str] = {}
        marker = ""
        while True:
            params: dict[str, str] = {"uploadId": upload_id, "max-parts": "1000"}
            if marker:
                params["part-number-marker"] = marker
            url = self._url(path, params)
            try:
                xml = self._request("GET", url)
            except FileNotFound as exc:
                # ListParts 的 404 只有一种含义：任务不存在了
                raise _NoSuchUpload(upload_id) from exc

            root = ET.fromstring(xml)
            for part in root.findall(f"{_S3_NS}Part") or root.findall("Part"):
                number = int(
                    part.findtext(f"{_S3_NS}PartNumber")
                    or part.findtext("PartNumber")
                    or "0"
                )
                etag = (
                    part.findtext(f"{_S3_NS}ETag") or part.findtext("ETag") or ""
                ).strip('"')
                if number:
                    parts[number] = etag

            truncated = (
                root.findtext(f"{_S3_NS}IsTruncated")
                or root.findtext("IsTruncated")
                or ""
            ).lower() == "true"
            if not truncated:
                break
            marker = (
                root.findtext(f"{_S3_NS}NextPartNumberMarker")
                or root.findtext("NextPartNumberMarker")
                or ""
            )
            if not marker:
                break
        return parts

    def _upload_part(self, path: str, upload_id: str, part_number: int, data: bytes) -> str:
        """上传单个分片，返回其 ETag。"""
        url = self._url(
            path, {"partNumber": str(part_number), "uploadId": upload_id}
        )
        headers = {"Content-Type": "application/octet-stream"}
        payload_hash = hashlib.sha256(data).hexdigest()
        if self.signature_version == "oss-hmac-sha1":
            headers["Content-MD5"] = __import__("base64").b64encode(
                hashlib.md5(data, usedforsecurity=False).digest()
            ).decode("ascii")
        try:
            resp = self._request("PUT", url, body=data, headers=headers, stream=True)
        except FileNotFound as exc:
            # UploadPart 的 404 只有一种含义：上传任务已不存在
            raise _NoSuchUpload(upload_id) from exc
        etag = ""
        try:
            etag = (resp.getheader("ETag") or "").strip('"')
        finally:
            resp.read()
            resp.close()
        if not etag:
            # 有些实现不返回 ETag 头，从响应体里找
            raise OssError(f"分片 {part_number} 上传后未返回 ETag")
        return etag

    def _complete_multipart(self, path: str, upload_id: str, parts: list[dict[str, Any]]) -> None:
        """提交分片列表，完成上传。"""
        url = self._url(path, {"uploadId": upload_id})
        items = "".join(
            "<Part><PartNumber>%d</PartNumber><ETag>\"%s\"</ETag></Part>"
            % (p["PartNumber"], p["ETag"])
            for p in parts
        )
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<CompleteMultipartUpload>%s</CompleteMultipartUpload>" % items
        ).encode("utf-8")
        self._request("POST", url, body=body, headers={"Content-Type": "application/xml"})

    def _abort_multipart(self, path: str, upload_id: str) -> None:
        """放弃分片上传，清理服务端残留。"""
        url = self._url(path, {"uploadId": upload_id})
        try:
            self._request("DELETE", url)
        except Exception:
            pass

    def _download_to(self, path: str, target: str, *, progress: Any = None) -> str:
        """流式下载到指定路径，内存占用与文件大小无关。"""
        info = self._stat(path)
        if info.is_dir:
            raise NotAFile(f"目标是目录：{path}")
        parent = os.path.dirname(os.path.abspath(target))
        if parent:
            os.makedirs(parent, exist_ok=True)
        url = self._url(path)
        resp = self._request("GET", url, stream=True)
        done = 0
        with open(target, "wb") as fh:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, info.size)
        resp.close()
        if progress:
            progress(done, done)
        return target

    def _delete(self, path: str, recursive: bool = False) -> None:
        key = self._key_of(path)
        if recursive:
            prefix = key if key.endswith("/") else key + "/"
            for entry in self._iter_objects(prefix):
                target = entry["key"]
                if target.endswith("/"):
                    continue
                self._request("DELETE", self._url(self._rel_of(target)))
            return
        url = self._url(path)
        try:
            self._request("DELETE", url)
        except OssError as exc:
            if "NoSuchKey" in str(exc) or "404" in str(exc):
                raise FileNotFound(f"路径不存在：{path}") from exc
            raise

    def _makedirs(self, path: str, exist_ok: bool = True) -> None:
        """对象存储没有真实目录，这里写入一个零字节的目录标记对象。

        目录标记的 key 必须以 ``/`` 结尾，否则服务端会把它当成普通对象。
        """
        key = self._key_of(path)
        if not key.endswith("/"):
            key += "/"
        # _url 内部用 norm_path 会吃掉结尾斜杠，这里直接拼 key 保证保留
        quoted = "/".join(uri_encode(part) for part in key.split("/") if part)
        url = f"{self._scheme()}://{self._host()}{self._base_path()}/{quoted}"
        if key.endswith("/") and not url.endswith("/"):
            url += "/"
        try:
            self._request("PUT", url, body=b"", headers={"Content-Type": "application/octet-stream"})
        except OssError as exc:
            if not exist_ok:
                raise StorageError(f"目录已存在或创建失败：{path}（{exc}）") from exc

    # ------------------------------------------------------------------
    # 对象存储特有能力
    # ------------------------------------------------------------------
    def copy(self, src: str, dst: str) -> FileInfo:
        """服务端复制（CopyObject），不下载再上传。"""
        source_key = self._key_of(src)
        target_key = self._key_of(dst)
        url = self._url(dst)
        headers = {
            "x-amz-copy-source": f"/{self.bucket}/{uri_encode(source_key)}",
            "Content-Type": "application/octet-stream",
        }
        self._request("PUT", url, headers=headers)
        return FileInfo(
            path=norm_path(self._rel_of(target_key)),
            storage=self.kind,
            extra={"key": target_key},
        )

    def move(self, src: str, dst: str) -> FileInfo:
        """复制后删除。"""
        info = self.copy(src, dst)
        self._delete(src, recursive=False)
        return info

    def signed_url(self, path: str, expires: int = 3600) -> str:
        """生成带签名的临时下载链接（S3 预签名 URL）。"""
        key = self._key_of(path)
        parsed = urllib.parse.urlsplit(self._url(path))
        now = _dt.datetime.now(_dt.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        scope = f"{date_stamp}/{self.region}/s3/aws4_request"
        credential = f"{self.access_key_id}/{scope}"

        query = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": credential,
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": str(expires),
            "X-Amz-SignedHeaders": "host",
        }
        canonical_query = "&".join(
            f"{uri_encode(k)}={uri_encode(v)}" for k, v in sorted(query.items())
        )
        canonical_request = "\n".join(
            [
                "GET",
                _canonical_uri(parsed.path),
                canonical_query,
                f"host:{parsed.netloc}\n",
                "host",
                "UNSIGNED-PAYLOAD",
            ]
        )
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                # 注意：预签名（query 签名）必须用标准格式，**不能**套用 cosv4。
                # COS 对 header 签名要求时间戳留空，对 query 签名却要求标准格式，
                # 两者行为不一致（实测）。
                amz_date,
                scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        key = ("AWS4" + self.access_key_secret).encode("utf-8")
        for part in (date_stamp, self.region, "s3", "aws4_request"):
            key = hmac.new(key, part.encode("utf-8"), hashlib.sha256).digest()
        signature = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{canonical_query}&X-Amz-Signature={signature}"

    # ------------------------------------------------------------------
    # 校验和
    # ------------------------------------------------------------------
    def checksum(self, path: str, algorithm: str = "md5") -> str:
        """计算远端对象的校验和。

        Args:
            path: 远端文件路径。
            algorithm: ``md5`` 或 ``sha256``。MD5 直接用 ETag（单次 PUT 时
                ETag 即 MD5）；SHA256 需下载内容计算。

        Returns:
            十六进制摘要。
        """
        algo = algorithm.lower()
        if algo == "md5":
            etag = self._stat(path).etag
            if etag and "-" not in etag:
                return etag.lower()
            # 分片上传的 ETag 形如 "<hash>-<n>"，不是文件 MD5，改用下载计算
        elif algo != "sha256":
            raise ValueError(f"不支持的算法：{algorithm}")

        digest = hashlib.new(algo)
        resp = self._request("GET", self._url(path), stream=True)
        try:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        finally:
            resp.close()
        return digest.hexdigest()

    def verify(self, path: str, expected: str, algorithm: str = "md5") -> bool:
        """校验远端对象是否与给定的摘要一致。

        Args:
            path: 远端文件路径。
            expected: 期望的十六进制摘要。
            algorithm: 摘要算法。
        """
        actual = self.checksum(path, algorithm)
        return actual.lower() == expected.lower()

    def checksum_local(self, local_path: str, algorithm: str = "md5") -> str:
        """计算本地文件的摘要（流式，不占内存）。"""
        digest = hashlib.new(algorithm.lower())
        with open(local_path, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def verify_local(
        self, local_path: str, expected: str, algorithm: str = "md5"
    ) -> bool:
        """校验本地文件摘要。"""
        return self.checksum_local(local_path, algorithm).lower() == expected.lower()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def close(self) -> None:
        """关闭 HTTP 连接池。"""
        self._pool.close()

    def __enter__(self) -> "OssStorage":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
