#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "LTCUSDT", "XRPUSDT",
    "BNBUSDT", "BCHUSDT", "ADAUSDT", "DOGEUSDT",
]
START_YEAR, START_MONTH = 2021, 1
END_YEAR, END_MONTH = 2025, 10
BASE = "https://data.binance.vision/data/spot/monthly/klines"
CACHE = Path("data/cache/binance/spot/monthly/klines_1d")
OUT = Path("artifacts/HARMONY-INFRA-0007")
MIN_ROWS = 1000
MIN_MONTHS = 36


def months():
    y, m = START_YEAR, START_MONTH
    out = []
    while (y, m) <= (END_YEAR, END_MONTH):
        out.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def fetch(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": "Harmony/INFRA-0007"})
    with urlopen(req, timeout=120) as r:
        return r.read()


def expected_checksum(text: str, filename: str) -> str:
    m = re.search(r"\b([0-9a-fA-F]{64})\b", text)
    if not m:
        raise ValueError(f"No SHA-256 checksum found for {filename}: {text[:200]!r}")
    return m.group(1).lower()


def normalize_open_time(raw: int) -> str:
    if raw >= 10**14:
        seconds = raw / 1_000_000
    else:
        seconds = raw / 1_000
    return datetime.fromtimestamp(seconds, tz=timezone.utc).date().isoformat()


def download_one(symbol: str, year: int, month: int):
    ym = f"{year:04d}-{month:02d}"
    filename = f"{symbol}-1d-{ym}.zip"
    rel = Path(symbol) / "1d" / filename
    dest = CACHE / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    zip_url = f"{BASE}/{symbol}/1d/{filename}"
    checksum_url = zip_url + ".CHECKSUM"

    checksum_text = fetch(checksum_url).decode("utf-8", errors="replace")
    expected = expected_checksum(checksum_text, filename)

    if dest.exists():
        archive = dest.read_bytes()
        source = "cache"
    else:
        archive = fetch(zip_url)
        dest.write_bytes(archive)
        source = "download"

    actual = sha256_bytes(archive)
    if actual != expected:
        dest.unlink(missing_ok=True)
        raise ValueError(f"SHA-256 mismatch {filename}: expected {expected}, got {actual}")

    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        csv_names = [n for n in names if n.lower().endswith(".csv")]
        if len(csv_names) != 1:
            raise ValueError(f"{filename}: expected one CSV member, found {csv_names}")
        member = csv_names[0]
        raw = zf.read(member).decode("utf-8")
        rows = list(csv.reader(io.StringIO(raw)))
    parsed = []
    for row in rows:
        if not row or not row[0].strip().isdigit():
            continue
        if len(row) < 6:
            raise ValueError(f"{filename}: malformed kline row")
        ts = int(row[0])
        parsed.append((normalize_open_time(ts), float(row[4])))

    if not parsed:
        raise ValueError(f"{filename}: no kline rows")
    parsed.sort()
    dates = [d for d, _ in parsed]
    if len(dates) != len(set(dates)):
        raise ValueError(f"{filename}: duplicate daily timestamps")
    if dates != sorted(dates):
        raise ValueError(f"{filename}: timestamps not monotonic")

    return {
        "symbol": symbol,
        "year": year,
        "month": month,
        "filename": filename,
        "zip_sha256": actual,
        "expected_sha256": expected,
        "source": source,
        "rows": len(parsed),
        "first_date": dates[0],
        "last_date": dates[-1],
        "dates": dates,
        "closes": dict(parsed),
        "url": zip_url,
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = [(s, y, m) for s in SYMBOLS for y, m in months()]
    results = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(download_one, *job) for job in jobs]
        for fut in as_completed(futures):
            results.append(fut.result())

    by_symbol = {s: [] for s in SYMBOLS}
    for r in sorted(results, key=lambda x: (x["symbol"], x["year"], x["month"])):
        by_symbol[r["symbol"]].append(r)

    common = None
    for symbol in SYMBOLS:
        ds = set()
        for r in by_symbol[symbol]:
            ds.update(r["dates"])
        common = ds if common is None else common & ds
    common_dates = sorted(common or set())

    months_common = set(d[:7] for d in common_dates)
    if len(common_dates) < MIN_ROWS:
        raise SystemExit(f"Common history too short: {len(common_dates)} < {MIN_ROWS}")
    if len(months_common) < MIN_MONTHS:
        raise SystemExit(f"Common history months too short: {len(months_common)} < {MIN_MONTHS}")

    # Exact intersection is intentionally used: no forward filling or synthetic observations.
    panel_path = OUT / "panel.csv"
    close_maps = {
        s: {d: px for r in by_symbol[s] for d, px in r["closes"].items()}
        for s in SYMBOLS
    }
    with panel_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date"] + [s + "_close" for s in SYMBOLS])
        for d in common_dates:
            w.writerow([d] + [f"{close_maps[s][d]:.17g}" for s in SYMBOLS])

    panel_sha = sha256_bytes(panel_path.read_bytes())
    manifest = {
        "experiment_id": "HARMONY-INFRA-0007",
        "source": {
            "publisher": "Binance Public Data",
            "archive_family": "spot_monthly_klines",
            "interval": "1d",
            "checksum_sidecars_verified": True,
            "source_family_fixed": True,
        },
        "universe": {
            "policy": "fixed_preexisting_research_basket",
            "symbols": SYMBOLS,
            "not_exhaustive_historical_market_universe": True,
        },
        "period": {"start_month": "2021-01", "end_month": "2025-10"},
        "files": [
            {k: r[k] for k in (
                "symbol","year","month","filename","zip_sha256",
                "expected_sha256","source","rows","first_date","last_date","url"
            )}
            for r in sorted(results, key=lambda x: (x["symbol"], x["year"], x["month"]))
        ],
        "common_history": {
            "row_count": len(common_dates),
            "month_count": len(months_common),
            "start": common_dates[0],
            "end": common_dates[-1],
            "missing_common_dates": 0,
        },
        "panel_sha256": panel_sha,
        "panel_path": str(panel_path),
        "gate": {
            "minimum_common_daily_rows": MIN_ROWS,
            "minimum_common_months": MIN_MONTHS,
            "pass": len(common_dates) >= MIN_ROWS and len(months_common) >= MIN_MONTHS,
        },
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    provenance = {
        "experiment_id": "HARMONY-INFRA-0007",
        "reproducibility": {
            "script_sha256": sha256_bytes(Path(__file__).read_bytes()),
            "archive_checksum_sidecars_verified": True,
            "one_archive_family_only": True,
            "current_exchange_universe_discovery": False,
            "forward_fill_or_synthetic_rows": False,
        },
        "panel_sha256": panel_sha,
        "manifest_sha256": sha256_bytes((OUT / "manifest.json").read_bytes()),
    }
    (OUT / "provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest["common_history"], indent=2, sort_keys=True))
    print(json.dumps(manifest["gate"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
