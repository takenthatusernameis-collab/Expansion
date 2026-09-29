"""Deterministic dataset materialization and fingerprinting for Harmony."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.request import urlopen


@dataclass(frozen=True)
class MaterializedDataset:
    source_url: str
    path: str
    size_bytes: int
    md5: str
    sha256: str

    @property
    def content_identity(self) -> str:
        return self.sha256


def _hash_file(path: Path, algorithm: str) -> str:
    h = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def materialize(
    source_url: str,
    destination: str | Path,
    *,
    expected_md5: str | None = None,
    expected_sha256: str | None = None,
    timeout_seconds: int = 60,
) -> MaterializedDataset:
    """Reuse verified local bytes or download, then fingerprint the exact artifact."""

    if not source_url:
        raise ValueError("source_url must be non-empty")

    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Contraction's efficiency objective implies: never redownload bytes that
    # already exist and whose identity can be verified.
    if path.exists():
        md5 = _hash_file(path, "md5")
        sha256 = _hash_file(path, "sha256")
        if expected_md5 is not None and md5.lower() != expected_md5.lower():
            raise ValueError(
                f"Existing-file MD5 mismatch: expected {expected_md5.lower()}, got {md5.lower()}"
            )
        if expected_sha256 is not None and sha256.lower() != expected_sha256.lower():
            raise ValueError(
                f"Existing-file SHA-256 mismatch: expected {expected_sha256.lower()}, got {sha256.lower()}"
            )
        return MaterializedDataset(
            source_url=source_url,
            path=str(path),
            size_bytes=path.stat().st_size,
            md5=md5,
            sha256=sha256,
        )

    with urlopen(source_url, timeout=timeout_seconds) as response:
        path.write_bytes(response.read())

    md5 = _hash_file(path, "md5")
    sha256 = _hash_file(path, "sha256")
    if expected_md5 is not None and md5.lower() != expected_md5.lower():
        path.unlink(missing_ok=True)
        raise ValueError(
            f"MD5 mismatch: expected {expected_md5.lower()}, got {md5.lower()}"
        )

    if expected_sha256 is not None and sha256.lower() != expected_sha256.lower():
        path.unlink(missing_ok=True)
        raise ValueError(
            f"SHA-256 mismatch: expected {expected_sha256.lower()}, got {sha256.lower()}"
        )

    return MaterializedDataset(
        source_url=source_url,
        path=str(path),
        size_bytes=path.stat().st_size,
        md5=md5,
        sha256=sha256,
    )


def manifest(dataset: MaterializedDataset, metadata: Mapping[str, object]) -> str:
    record = {
        "source_url": dataset.source_url,
        "path": dataset.path,
        "size_bytes": dataset.size_bytes,
        "md5": dataset.md5,
        "sha256": dataset.sha256,
        "metadata": dict(metadata),
    }
    return json.dumps(record, sort_keys=True, indent=2) + "\n"
