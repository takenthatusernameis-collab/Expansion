import hashlib
from pathlib import Path

from harmony_backtest.archive_materializer import (
    ArchiveSpec,
    deterministic_manifest,
    materialize_archive,
    sha256_file,
)


def test_existing_verified_file_is_reused(tmp_path: Path, monkeypatch) -> None:
    payload = b"harmony-archive"
    path = tmp_path / "a.zip"
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()

    monkeypatch.setattr(
        "harmony_backtest.archive_materializer.fetch_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("source must not be fetched when no checksum is declared")
        ),
    )

    spec = ArchiveSpec(
        key="BTCUSDT-2026-01",
        source_url="fixture://never-download",
        destination=str(path),
    )
    identity = materialize_archive(spec)
    assert identity.reused_existing is True
    assert identity.sha256 == digest
    assert sha256_file(path) == digest


def test_existing_file_wrong_checksum_is_refused(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "a.zip"
    path.write_bytes(b"bad-cache")
    expected = "0" * 64

    def fake_fetch(url: str, timeout_seconds: int = 120) -> bytes:
        if url.endswith("checksum"):
            return (expected + "\n").encode()
        raise AssertionError("mismatched cached bytes must be removed before source reuse")

    monkeypatch.setattr(
        "harmony_backtest.archive_materializer.fetch_bytes",
        fake_fetch,
    )
    spec = ArchiveSpec(
        key="BTCUSDT-2026-01",
        source_url="fixture://source",
        destination=str(path),
        checksum_url="fixture://checksum",
    )
    try:
        materialize_archive(spec)
    except AssertionError as exc:
        assert "mismatched cached bytes" in str(exc)
    else:
        raise AssertionError("expected source fetch after mismatch")


def test_manifest_sorting_is_deterministic() -> None:
    from harmony_backtest.archive_materializer import ArchiveIdentity

    rows = [
        ArchiveIdentity("b", "u2", "p2", 2, "2" * 64, None, True),
        ArchiveIdentity("a", "u1", "p1", 1, "1" * 64, None, False),
    ]
    output = deterministic_manifest("D1", {"end": "2026-08-31"}, rows)
    assert output.index('"key": "a"') < output.index('"key": "b"')
