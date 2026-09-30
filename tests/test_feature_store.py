import gzip
import hashlib
import json
from pathlib import Path

from harmony_backtest.feature_store import (
    FeatureRow,
    canonical_feature_bytes,
    feature_store_manifest,
    write_feature_store,
)


def test_feature_bytes_are_order_independent() -> None:
    rows_a = [
        FeatureRow("BTCUSDT", "2021-01-02", {"z": 2.0, "a": 1.0}),
        FeatureRow("ADAUSDT", "2021-01-01", {"z": 3.0}),
    ]
    rows_b = list(reversed(rows_a))
    assert canonical_feature_bytes(rows_a) == canonical_feature_bytes(rows_b)


def test_feature_store_is_deterministic(tmp_path: Path) -> None:
    rows = [
        FeatureRow("BTCUSDT", "2021-01-01", {"mean_7d": None, "mean_1d": 0.1}),
        FeatureRow("BTCUSDT", "2021-01-02", {"mean_7d": 0.2, "mean_1d": 0.3}),
    ]
    p = tmp_path / "features.jsonl.gz"
    sha = write_feature_store(p, rows)
    assert sha == hashlib.sha256(p.read_bytes()).hexdigest()
    with gzip.open(p, "rb") as handle:
        decoded = handle.read().decode()
    assert decoded.count("\n") == 2


def test_feature_manifest_is_canonical() -> None:
    output = feature_store_manifest(
        dataset_manifest_sha256="a" * 64,
        feature_set_id="funding_carry_v1",
        feature_revision="r1",
        feature_store_sha256="b" * 64,
        row_count=16,
    )
    parsed = json.loads(output)
    assert parsed["feature_store_sha256"] == "b" * 64
