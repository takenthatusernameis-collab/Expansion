"""Deterministic normalized daily research store for shared candidate execution.

The store is derived once from verified raw Binance archives. Candidate runners consume the
normalized store instead of reopening the same monthly ZIP files for every strategy.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


@dataclass(frozen=True)
class DailyMarketRow:
    symbol: str
    date: str
    close: float
    quote_volume: float
    taker_buy_quote_volume: float


STORE_COLUMNS = (
    "symbol",
    "date",
    "close",
    "quote_volume",
    "taker_buy_quote_volume",
)


def _read_zip_csv(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"unexpected archive shape: {path}")
        if archive.testzip() is not None:
            raise ValueError(f"archive CRC failure: {path}")
        return list(csv.reader(io.StringIO(archive.read(names[0]).decode("utf-8"))))


def load_monthly_klines(
    root: str | Path,
    symbols: Sequence[str],
    start_month: tuple[int, int],
    end_month: tuple[int, int],
) -> list[DailyMarketRow]:
    root_path = Path(root)
    rows: list[DailyMarketRow] = []
    months: list[tuple[int, int]] = []
    y, m = start_month
    while (y, m) <= end_month:
        months.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1

    for symbol in symbols:
        for year, month in months:
            path = root_path / "klines" / symbol / "1d" / f"{symbol}-1d-{year:04d}-{month:02d}.zip"
            if not path.is_file():
                raise FileNotFoundError(path)
            raw = _read_zip_csv(path)
            for row in raw:
                if not row or not row[0].isdigit():
                    continue
                if len(row) < 12:
                    raise ValueError(f"short kline row: {path}")
                # Binance USD-M 1d kline schema:
                # open time, OHLC, volume, close time, quote volume, trades,
                # taker buy base volume, taker buy quote volume, ...
                from datetime import datetime, timezone
                date = datetime.fromtimestamp(
                    int(row[0]) / 1000.0, timezone.utc
                ).date().isoformat()
                rows.append(
                    DailyMarketRow(
                        symbol=symbol,
                        date=date,
                        close=float(row[4]),
                        quote_volume=float(row[7]),
                        taker_buy_quote_volume=float(row[11]),
                    )
                )

    rows.sort(key=lambda r: (r.symbol, r.date))
    return rows


def canonical_store_bytes(rows: Iterable[DailyMarketRow]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(STORE_COLUMNS)
    ordered = sorted(rows, key=lambda r: (r.symbol, r.date))
    for row in ordered:
        writer.writerow(
            [
                row.symbol,
                row.date,
                format(row.close, ".17g"),
                format(row.quote_volume, ".17g"),
                format(row.taker_buy_quote_volume, ".17g"),
            ]
        )
    return stream.getvalue().encode("utf-8")


def write_daily_store(path: str | Path, rows: Iterable[DailyMarketRow]) -> str:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_store_bytes(rows)
    with destination.open("wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as compressed:
            compressed.write(payload)
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def read_daily_store(path: str | Path) -> list[DailyMarketRow]:
    with gzip.open(path, "rt", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != STORE_COLUMNS:
            raise ValueError("daily store schema mismatch")
        rows = [
            DailyMarketRow(
                symbol=r["symbol"],
                date=r["date"],
                close=float(r["close"]),
                quote_volume=float(r["quote_volume"]),
                taker_buy_quote_volume=float(r["taker_buy_quote_volume"]),
            )
            for r in reader
        ]
    return rows


def store_manifest(
    *,
    source_manifest_sha256: str,
    store_sha256: str,
    symbols: Sequence[str],
    start: str,
    end: str,
    row_count: int,
    revision: str,
) -> str:
    record = {
        "dataset_layer": "normalized_daily_market_store",
        "source_manifest_sha256": source_manifest_sha256,
        "store_sha256": store_sha256,
        "symbols": list(symbols),
        "start": start,
        "end": end,
        "row_count": row_count,
        "revision": revision,
    }
    return json.dumps(record, sort_keys=True, indent=2) + "\n"
