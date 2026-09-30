import hashlib
import json
from pathlib import Path

from harmony_backtest.daily_store import load_monthly_klines, store_manifest, write_daily_store

SYMBOLS = ["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
ROOT = Path("data/cache/binance/futures_um/deep_history_2019")
STORE = Path("data/cache/research/daily_8sym_v1.csv.gz")
OUT = Path("artifacts/HARMONY-DAILY-STORE-001")
SOURCE_CACHE_KEY = "harmony-binance-um-deep-history-2019-2025-10-v1-36777989764"
REVISION = "daily_8sym_v1_r1"

rows = load_monthly_klines(ROOT, SYMBOLS, (2020, 1), (2025, 10))

dates = sorted({row.date for row in rows})
common = set(dates)
by_symbol = {s: set() for s in SYMBOLS}
for row in rows:
    by_symbol[row.symbol].add(row.date)
for s in SYMBOLS:
    common &= by_symbol[s]

rows = [row for row in rows if row.date in common]
store_sha = write_daily_store(STORE, rows)

source_identity = {
    "raw_cache_key": SOURCE_CACHE_KEY,
    "raw_cache_root": str(ROOT),
    "protocol": "HARMONY-DEEP-HISTORY-2019-001",
}
source_manifest_sha = hashlib.sha256(
    json.dumps(source_identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()

manifest = store_manifest(
    source_manifest_sha256=source_manifest_sha,
    store_sha256=store_sha,
    symbols=SYMBOLS,
    start=min(common),
    end=max(common),
    row_count=len(rows),
    revision=REVISION,
)
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "manifest.json").write_text(manifest, encoding="utf-8")
STORE_MANIFEST = STORE.with_name(STORE.name + ".manifest.json")
STORE_MANIFEST.write_text(manifest, encoding="utf-8")
(OUT / "status.json").write_text(
    json.dumps(
        {
            "dataset_id": "HARMONY-DAILY-STORE-001",
            "store_path": str(STORE),
            "store_sha256": store_sha,
            "row_count": len(rows),
            "common_start": min(common),
            "common_end": max(common),
            "source_manifest_sha256": source_manifest_sha,
            "raw_cache_key": SOURCE_CACHE_KEY,
            "revision": REVISION,
        },
        sort_keys=True,
        indent=2,
    ) + "\n",
    encoding="utf-8",
)
print((OUT / "status.json").read_text())
