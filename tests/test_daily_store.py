import gzip
from pathlib import Path

from harmony_backtest.daily_store import DailyMarketRow, read_daily_store, write_daily_store


def test_daily_store_uses_taker_buy_quote_volume_field_ten(tmp_path: Path):
    rows = [DailyMarketRow("BTCUSDT", "2025-01-01", 100.0, 500.0, 300.0)]
    path = tmp_path / "store.csv.gz"
    write_daily_store(path, rows)
    loaded = read_daily_store(path)
    assert loaded[0].taker_buy_quote_volume == 300.0


def test_daily_store_is_byte_deterministic(tmp_path: Path):
    rows_a = [
        DailyMarketRow("ETHUSDT", "2025-01-02", 200.0, 1000.0, 450.0),
        DailyMarketRow("BTCUSDT", "2025-01-01", 100.0, 1200.0, 650.0),
    ]
    rows_b = list(reversed(rows_a))
    p1 = tmp_path / "a.csv.gz"
    p2 = tmp_path / "b.csv.gz"
    sha1 = write_daily_store(p1, rows_a)
    sha2 = write_daily_store(p2, rows_b)
    assert p1.read_bytes() == p2.read_bytes()
    assert sha1 == sha2
    with gzip.open(p1, "rt", encoding="utf-8") as handle:
        assert handle.readline().startswith("symbol,date,close,quote_volume")
