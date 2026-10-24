"""Object storage abstraction: local filesystem (dev) or any S3-compatible service."""
from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path

from app.config import get_settings


class Storage(ABC):
    name = "base"

    @abstractmethod
    def put_file(self, key: str, src_path: str, content_type: str) -> int: ...

    @abstractmethod
    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> int: ...

    @abstractmethod
    def read_bytes(self, key: str) -> bytes: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def local_path(self, key: str, dest_dir: str) -> str:
        """Return a local filesystem path for the object (downloading if needed)."""

    def presigned_url(self, key: str, ttl_seconds: int, filename: str) -> str | None:
        """Direct download URL, or None if the API should stream the file itself."""
        return None


def _safe_key(key: str) -> str:
    parts = [p for p in key.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    if not parts:
        raise ValueError("invalid storage key")
    return "/".join(parts)


class LocalStorage(Storage):
    name = "local"

    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key: str) -> Path:
        p = (self.root / _safe_key(key)).resolve()
        if self.root not in p.parents:
            raise ValueError("invalid storage key")
        return p

    def put_file(self, key: str, src_path: str, content_type: str) -> int:
        dst = self.path(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src_path, dst)
        return dst.stat().st_size

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> int:
        dst = self.path(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        return len(data)

    def read_bytes(self, key: str) -> bytes:
        return self.path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self.path(key).exists()

    def delete(self, key: str) -> None:
        self.path(key).unlink(missing_ok=True)

    def local_path(self, key: str, dest_dir: str) -> str:
        return str(self.path(key))


class S3Storage(Storage):
    name = "s3"

    def __init__(self):
        import boto3

        s = get_settings()
        if not s.s3_bucket:
            raise ValueError("S3_BUCKET is required for STORAGE_BACKEND=s3")
        self.bucket = s.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url,
            region_name=s.s3_region,
            aws_access_key_id=s.s3_access_key_id,
            aws_secret_access_key=s.s3_secret_access_key,
        )

    def put_file(self, key: str, src_path: str, content_type: str) -> int:
        key = _safe_key(key)
        self.client.upload_file(src_path, self.bucket, key, ExtraArgs={"ContentType": content_type})
        return Path(src_path).stat().st_size

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> int:
        self.client.put_object(Bucket=self.bucket, Key=_safe_key(key), Body=data, ContentType=content_type)
        return len(data)

    def read_bytes(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=_safe_key(key))["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=_safe_key(key))
            return True
        except Exception:  # noqa: BLE001 - botocore ClientError
            return False

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=_safe_key(key))

    def local_path(self, key: str, dest_dir: str) -> str:
        dst = Path(dest_dir) / Path(_safe_key(key)).name
        self.client.download_file(self.bucket, _safe_key(key), str(dst))
        return str(dst)

    def presigned_url(self, key: str, ttl_seconds: int, filename: str) -> str | None:
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket, "Key": _safe_key(key),
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
            },
            ExpiresIn=ttl_seconds,
        )


@lru_cache
def get_storage() -> Storage:
    s = get_settings()
    if s.storage_backend == "s3":
        return S3Storage()
    return LocalStorage(s.storage_local_dir)
