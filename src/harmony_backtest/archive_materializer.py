"""Reusable checksum-aware archive materialization for Harmony research campaigns."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class ArchiveSpec:
    key: str
    source_url: str
    destination: str
    checksum_url: str | None = None


@dataclass(frozen=True)
class ArchiveIdentity:
    key: str
    source_url: str
    path: str
    size_bytes: int
    sha256: str
    expected_sha256: str | None
    reused_existing: bool


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_bytes(url: str, timeout_seconds: int = 120) -> bytes:
    request = Request(url, headers={"User-Agent": "Harmony/RESEARCH-SUBSTRATE-001"})
    with urlopen(request, timeout=timeout_seconds) as response:
        return response.read()


def read_expected_checksum(url: str, timeout_seconds: int = 120) -> str:
    raw = fetch_bytes(url, timeout_seconds).decode("utf-8", "replace")
    token = raw.strip().split()[0].lower()
    if len(token) != 64:
        raise ValueError(f"invalid upstream SHA-256 checksum: {url}")
    int(token, 16)
    return token


def materialize_archive(spec: ArchiveSpec, *, timeout_seconds: int = 120) -> ArchiveIdentity:
    if not spec.key or not spec.source_url or not spec.destination:
        raise ValueError("key, source_url, and destination are required")

    path = Path(spec.destination)
    path.parent.mkdir(parents=True, exist_ok=True)

    expected = read_expected_checksum(spec.checksum_url, timeout_seconds) if spec.checksum_url else None

    if path.exists():
        actual = sha256_file(path)
        if expected is not None and actual != expected:
            path.unlink()
        else:
            return ArchiveIdentity(
                key=spec.key,
                source_url=spec.source_url,
                path=str(path),
                size_bytes=path.stat().st_size,
                sha256=actual,
                expected_sha256=expected,
                reused_existing=True,
            )

    payload = fetch_bytes(spec.source_url, timeout_seconds)
    path.write_bytes(payload)
    actual = sha256_file(path)

    if expected is not None and actual != expected:
        path.unlink(missing_ok=True)
        raise ValueError(
            f"downloaded SHA-256 mismatch for {spec.key}: "
            f"expected {expected}, got {actual}"
        )

    return ArchiveIdentity(
        key=spec.key,
        source_url=spec.source_url,
        path=str(path),
        size_bytes=path.stat().st_size,
        sha256=actual,
        expected_sha256=expected,
        reused_existing=False,
    )


def deterministic_manifest(
    dataset_id: str,
    scope: dict[str, object],
    identities: list[ArchiveIdentity],
) -> str:
    if not dataset_id:
        raise ValueError("dataset_id must be non-empty")
    rows = [
        {
            "key": item.key,
            "source_url": item.source_url,
            "path": item.path,
            "size_bytes": item.size_bytes,
            "sha256": item.sha256,
            "expected_sha256": item.expected_sha256,
            "reused_existing": item.reused_existing,
        }
        for item in sorted(identities, key=lambda x: x.key)
    ]
    payload = {"dataset_id": dataset_id, "scope": scope, "files": rows}
    return json.dumps(payload, sort_keys=True, indent=2) + "\n"
