import csv, hashlib, io, json, zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

SYMBOLS = ["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
START = "2025-11-01"
END = "2026-09-20"
MONTHS = [(2025,m) for m in range(11,13)] + [(2026,m) for m in range(1,10)]

ROOT = Path("data/cache/binance/futures_um/holdout_2025-11_2026-09")
KLINE_ROOT = ROOT / "klines"
FUND_ROOT = ROOT / "fundingRate"
OUT = Path("artifacts/HARMONY-HOLDOUT-DATA-001")
OUT.mkdir(parents=True, exist_ok=True)

KLINE_BASE = "https://data.binance.vision/data/futures/um/monthly/klines"
FUND_BASE = "https://data.binance.vision/data/futures/um/monthly/fundingRate"

def fetch(url):
    req = Request(url, headers={"User-Agent": "Harmony/HOLDOUT-DATA-001"})
    with urlopen(req, timeout=120) as r:
        return r.read()

def sha256(b):
    return hashlib.sha256(b).hexdigest()

def parse_day(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).date().isoformat()

def load_zip_rows(raw, filename):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise RuntimeError(f"{filename}: expected exactly one CSV member, got {names}")
        return list(csv.reader(io.StringIO(z.read(names[0]).decode("utf-8"))))

def verified_archive(url, path):
    try:
        checksum_text = fetch(url + ".CHECKSUM").decode("utf-8", "replace")
    except Exception as exc:
        raise RuntimeError(f"CHECKSUM_FETCH_FAILED: {url}.CHECKSUM :: {exc}") from exc
    expected = checksum_text.strip().split()[0].lower()
    if len(expected) != 64:
        raise RuntimeError(f"{url}: invalid checksum sidecar")
    if path.exists():
        raw = path.read_bytes()
        source = "cache"
    else:
        raw = fetch(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        source = "download"
    actual = sha256(raw)
    if actual != expected:
        path.unlink(missing_ok=True)
        raise RuntimeError(f"{url}: checksum mismatch {actual} != {expected}")
    return raw, source, expected, actual

def load_price(symbol, year, month):
    ym = f"{year:04d}-{month:02d}"
    name = f"{symbol}-1d-{ym}.zip"
    url = f"{KLINE_BASE}/{symbol}/1d/{name}"
    path = KLINE_ROOT / symbol / "1d" / name
    raw, source, expected, actual = verified_archive(url, path)
    rows = load_zip_rows(raw, name)
    dates = {}
    for r in rows:
        if r and r[0].isdigit():
            if len(r) < 6:
                raise RuntimeError(f"{name}: malformed kline row")
            dates[parse_day(int(r[0]))] = float(r[4])
    if not dates:
        raise RuntimeError(f"{name}: no price rows")
    return {
        "kind":"price","symbol":symbol,"year":year,"month":month,"filename":name,
        "url":url,"source":source,"expected_sha256":expected,"sha256":actual,
        "row_count":len(dates),"first_date":min(dates),"last_date":max(dates)
    }, dates

def load_funding(symbol, year, month):
    ym = f"{year:04d}-{month:02d}"
    name = f"{symbol}-fundingRate-{ym}.zip"
    url = f"{FUND_BASE}/{symbol}/{name}"
    path = FUND_ROOT / symbol / name
    raw, source, expected, actual = verified_archive(url, path)
    rows = load_zip_rows(raw, name)
    if not rows:
        raise RuntimeError(f"{name}: empty archive")
    h = {k.strip(): i for i, k in enumerate(rows[0])}
    for required in ("calc_time","funding_interval_hours"):
        if required not in h:
            raise RuntimeError(f"{name}: missing {required}")
    rate_col = "last_funding_rate" if "last_funding_rate" in h else ("fundingRate" if "fundingRate" in h else None)
    if rate_col is None:
        raise RuntimeError(f"{name}: missing funding rate column")
    events = []
    last_ts = None
    for r in rows[1:]:
        if not r:
            continue
        ts = int(r[h["calc_time"]])
        if last_ts is not None and ts <= last_ts:
            raise RuntimeError(f"{name}: non-increasing calc_time")
        last_ts = ts
        events.append({
            "calc_time": ts,
            "date": parse_day(ts),
            "funding_interval_hours": int(float(r[h["funding_interval_hours"]])),
            "last_funding_rate": float(r[h[rate_col]])
        })
    if not events:
        raise RuntimeError(f"{name}: no funding rows")
    return {
        "kind":"funding","symbol":symbol,"year":year,"month":month,"filename":name,
        "url":url,"source":source,"expected_sha256":expected,"sha256":actual,
        "row_count":len(events),"first_date":events[0]["date"],"last_date":events[-1]["date"],
        "native_intervals_hours":sorted({x["funding_interval_hours"] for x in events})
    }, events

price_meta = []
fund_meta = []
price_dates = {}
funding_events = {}

with ThreadPoolExecutor(max_workers=8) as ex:
    futures = []
    for s in SYMBOLS:
        for y,m in MONTHS:
            futures.append(ex.submit(load_price,s,y,m))
            futures.append(ex.submit(load_funding,s,y,m))
    for f in as_completed(futures):
        meta, payload = f.result()
        if meta["kind"] == "price":
            price_meta.append(meta)
            price_dates.setdefault(meta["symbol"], set()).update(payload)
        else:
            fund_meta.append(meta)
            funding_events.setdefault(meta["symbol"], []).extend(payload)

price_meta.sort(key=lambda x:(x["symbol"],x["year"],x["month"]))
fund_meta.sort(key=lambda x:(x["symbol"],x["year"],x["month"]))

common = set.intersection(*(price_dates[s] for s in SYMBOLS))
common = sorted(d for d in common if START <= d <= END)
if not common or common[0] != START or common[-1] != END:
    raise RuntimeError(f"common holdout panel mismatch: {common[:2]} ... {common[-2:]}")

for s in SYMBOLS:
    ev = sorted(e for e in funding_events[s] if START <= e["date"] <= END)
    if not ev:
        raise RuntimeError(f"{s}: no funding coverage inside holdout")
    funding_events[s] = ev

manifest = {
    "experiment_id":"HARMONY-HOLDOUT-DATA-001",
    "protocol":"HARMONY-HOLDOUT-PROTOCOL-001",
    "selection_data_end":"2025-10-31",
    "holdout_start":START,
    "holdout_end":END,
    "symbols":SYMBOLS,
    "required_months":[f"{y:04d}-{m:02d}" for y,m in MONTHS],
    "price_archives":price_meta,
    "funding_archives":fund_meta,
    "common_panel":{"start":common[0],"end":common[-1],"observations":len(common)},
    "native_funding_intervals_hours":{s:sorted({e["funding_interval_hours"] for e in funding_events[s]}) for s in SYMBOLS},
    "frontier_digest":"bfdbd2a4e3bd5fd393479ae93f821d13d8f23d455e6763a574ed664da80e4966",
    "holdout_outcomes_released":False
}
manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n"
(OUT / "holdout-manifest.json").write_bytes(manifest_bytes)
(OUT / "common-panel-dates.json").write_text(json.dumps(common, indent=2)+"\n")
(OUT / "funding-coverage.json").write_text(json.dumps({
    s:[{"date":e["date"],"calc_time":e["calc_time"],"funding_interval_hours":e["funding_interval_hours"],"last_funding_rate":e["last_funding_rate"]} for e in funding_events[s]]
    for s in SYMBOLS
}, indent=2, sort_keys=True)+"\n")
print(json.dumps({
    "experiment_id":"HARMONY-HOLDOUT-DATA-001",
    "common_panel_start":common[0],
    "common_panel_end":common[-1],
    "common_panel_observations":len(common),
    "price_archives":len(price_meta),
    "funding_archives":len(fund_meta),
    "manifest_sha256":sha256(manifest_bytes)
}, indent=2, sort_keys=True))
