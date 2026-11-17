"""Uniform blob access for local paths and ``gs://`` URIs."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def is_gcs(uri: str) -> bool:
    return uri.startswith("gs://")


def join(base: str, *parts: str) -> str:
    if is_gcs(base):
        return "/".join([base.rstrip("/"), *[p.strip("/") for p in parts]])
    return str(Path(base, *parts))


def _split_gcs(uri: str) -> tuple[str, str]:
    rest = uri[len("gs://") :]
    bucket, _, name = rest.partition("/")
    return bucket, name


_client: Any = None


def _gcs_client() -> Any:
    global _client
    if _client is None:
        from google.cloud import storage  # type: ignore[attr-defined]

        _client = storage.Client()
    return _client


def read_bytes(uri: str) -> bytes:
    if is_gcs(uri):
        bucket, name = _split_gcs(uri)
        return bytes(_gcs_client().bucket(bucket).blob(name).download_as_bytes())
    return Path(uri).read_bytes()


def read_text(uri: str) -> str:
    return read_bytes(uri).decode("utf-8")


def write_bytes(uri: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    if is_gcs(uri):
        bucket, name = _split_gcs(uri)
        _gcs_client().bucket(bucket).blob(name).upload_from_string(data, content_type=content_type)
        return
    path = Path(uri)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def write_text(uri: str, text: str, content_type: str = "text/plain; charset=utf-8") -> None:
    write_bytes(uri, text.encode("utf-8"), content_type)


def exists(uri: str) -> bool:
    if is_gcs(uri):
        bucket, name = _split_gcs(uri)
        return bool(_gcs_client().bucket(bucket).blob(name).exists())
    return Path(uri).exists()


def list_files(prefix: str) -> list[str]:
    """List file URIs under a local directory or a gs:// prefix (recursive, sorted)."""
    if is_gcs(prefix):
        bucket, name = _split_gcs(prefix)
        name = name.rstrip("/") + "/" if name else ""
        blobs = _gcs_client().list_blobs(bucket, prefix=name)
        return sorted(f"gs://{bucket}/{b.name}" for b in blobs if not b.name.endswith("/"))
    root = Path(prefix)
    if root.is_file():
        return [str(root)]
    return sorted(str(p) for p in root.rglob("*") if p.is_file())
