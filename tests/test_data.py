import hashlib
from pathlib import Path

import pytest

from harmony_backtest.data import MaterializedDataset, _hash_file, manifest


def test_hash_file_is_deterministic(tmp_path: Path) -> None:
    path = tmp_path / "fixture.bin"
    path.write_bytes(b"harmony-data")
    assert _hash_file(path, "md5") == hashlib.md5(b"harmony-data").hexdigest()
    assert _hash_file(path, "sha256") == hashlib.sha256(b"harmony-data").hexdigest()


def test_manifest_is_canonical_json() -> None:
    dataset = MaterializedDataset(
        source_url="fixture://data",
        path="data.csv",
        size_bytes=12,
        md5="a" * 32,
        sha256="b" * 64,
    )
    output = manifest(dataset, {"symbol": "BTC-USD", "timeframe": "1d"})
    assert '"sha256": "' + "b" * 64 in output
    assert '"symbol": "BTC-USD"' in output


def test_materialized_dataset_identity_is_sha256() -> None:
    dataset = MaterializedDataset(
        source_url="fixture://data",
        path="data.csv",
        size_bytes=1,
        md5="a" * 32,
        sha256="c" * 64,
    )
    assert dataset.content_identity == "c" * 64
