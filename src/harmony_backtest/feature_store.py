"""Deterministic persistent feature-store utilities for Harmony."""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


@dataclass(frozen=True)
class FeatureRow:
    symbol: str
    date: str
    values: Mapping[str, float | None]


def canonical_feature_bytes(rows: Iterable[FeatureRow]) -> bytes:
    ordered = sorted(
        (
            {"symbol": r.symbol, "date": r.date, "values": dict(sorted(r.values.items()))}
            for r in rows
        ),
        key=lambda x: (x["symbol"], x["date"]),
    )
    payload = b"".join(
        (json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        for row in ordered
    )
    return payload


def write_feature_store(path: str | Path, rows: Iterable[FeatureRow]) -> str:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical_feature_bytes(rows)

    with destination.open("wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as compressed:
            compressed.write(raw)

    return hashlib.sha256(destination.read_bytes()).hexdigest()


def feature_store_manifest(
    *,
    dataset_manifest_sha256: str,
    feature_set_id: str,
    feature_revision: str,
    feature_store_sha256: str,
    row_count: int,
) -> str:
    record = {
        "dataset_manifest_sha256": dataset_manifest_sha256,
        "feature_revision": feature_revision,
        "feature_set_id": feature_set_id,
        "feature_store_sha256": feature_store_sha256,
        "row_count": row_count,
    }
    return json.dumps(record, sort_keys=True, indent=2) + "\n"
