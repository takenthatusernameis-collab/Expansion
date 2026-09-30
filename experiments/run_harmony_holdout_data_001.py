import csv, hashlib, io, json, zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

SYMBOLS = ["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
START = "2025-11-01"
END = "2026-09-20"
MONTHS = [(2025,m) for m in range(11,13)] + [(2026,m) for m in range(1,9)]
PARTIAL_DAYS = [date(2026,9,1) + timedelta(days=i) for i in range(20)]

ROOT = Path("data/cache/binance/futures_um/holdout_2025-11_2026-09")
KLINE_ROOT = ROOT / "klines"
FUND_ROOT = ROOT / "fundingRate"
OUT = Path("artifacts/HARMONY-HOLDOUT-DATA-001")
OUT.mkdir(parents=True, exist_ok=True)

BASE = "https://data.binance.vision/data/futures/um"

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
    checksum_url = url + ".CHECKSUM"
    try:
        checksum_text = fetch(checksum_url).decode("utf-8", "replace")
    except Exception as exc:
        raise RuntimeError(f"CHECKSUM_FETCH_FAILED: {checksum_url} :: {exc}") from exc
    expected = checksum_text.strip().split()[0].lower()
    if len(expected) != 64:
        raise RuntimeError(f"{checksum_url}: invalid checksum sidecar")
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

def load_price(symbol, kind, stamp):
    if kind == "monthly":
        y,m = stamp
        ym = f"{y:04d}-{m:02d}"
        name = f"{symbol}-1d-{ym}.zip"
        url = f"{BASE}/monthly/klines/{symbol}/1d/{name}"
        path = KLINE_ROOT / "monthly" / symbol / "1d" / name
    else:
        d = stamp
        ds = d.isoformat()
        name = f"{symbol}-1d-{ds}.zip"
        url = f"{BASE}/daily/klines/{symbol}/1d/{name}"
        path = KLINE_ROOT / "daily" / symbol / "1d" / name
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
        "kind":"price","archive_frequency":kind,"symbol":symbol,
        "period": f"{stamp[0]:04d}-{stamp[1]:02d}" if kind=="monthly" else stamp.isoformat(),
        "filename":name,"url":url,"source":source,"expected_sha256":expected,
        "sha256":actual,"row_count":len(dates),"first_date":min(dates),"last_date":max(dates)
    }, dates

def load_funding_monthly(symbol, stamp):
    y,m = stamp
    ym = f"{y:04d}-{m:02d}"
    name = f"{symbol}-fundingRate-{ym}.zip"
    url = f"{BASE}/monthly/fundingRate/{symbol}/{name}"
    path = FUND_ROOT / "monthly" / symbol / name
    raw, source, expected, actual = verified_archive(url, path)
    rows = load_zip_rows(raw, name)
    if not rows:
        raise RuntimeError(f"{name}: empty archive")
    h = {k.strip(): i for i,k in enumerate(rows[0])}
    if not {"calc_time","funding_interval_hours"}.issubset(h):
        raise RuntimeError(f"{name}: missing native funding interval fields")
    rate_col = "last_funding_rate" if "last_funding_rate" in h else None
    if rate_col is None:
        raise RuntimeError(f"{name}: missing last_funding_rate")
    events=[]; last_ts=None
    for r in rows[1:]:
        if not r: continue
        ts=int(r[h["calc_time"]])
        if last_ts is not None and ts <= last_ts:
            raise RuntimeError(f"{name}: non-increasing calc_time")
        last_ts=ts
        events.append({
            "calc_time":ts,"date":parse_day(ts),
            "funding_interval_hours":int(float(r[h["funding_interval_hours"]])),
            "last_funding_rate":float(r[h[rate_col]])
        })
    if not events:
        raise RuntimeError(f"{name}: no funding rows")
    return {
        "kind":"funding","archive_frequency":"monthly","symbol":symbol,
        "period":ym,"filename":name,"url":url,"source":source,
        "expected_sha256":expected,"sha256":actual,"row_count":len(events),
        "first_date":events[0]["date"],"last_date":events[-1]["date"],
        "native_intervals_hours":sorted({x["funding_interval_hours"] for x in events})
    }, events

def load_funding_partial_archive(symbol):
    events=[]
    archive_meta=[]
    for d in PARTIAL_DAYS:
        ds=d.isoformat()
        name=f"{symbol}-fundingRate-{ds}.zip"
        url=f"{BASE}/daily/fundingRate/{symbol}/{name}"
        path=FUND_ROOT/"daily"/symbol/name
        if path.exists():
            raw=path.read_bytes(); source="cache"
        else:
            raw=fetch(url)
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(raw); source="download"
        local_sha=sha256(raw)
        rows=load_zip_rows(raw,name)
        if not rows:
            raise RuntimeError(f"{name}: empty archive")
        h={k.strip():i for i,k in enumerate(rows[0])}
        if "calc_time" not in h:
            raise RuntimeError(f"{name}: missing calc_time")
        rate_col="last_funding_rate" if "last_funding_rate" in h else ("fundingRate" if "fundingRate" in h else None)
        if rate_col is None:
            raise RuntimeError(f"{name}: missing funding rate column")
        for r in rows[1:]:
            if not r: continue
            ts=int(r[h["calc_time"]])
            events.append({
                "calc_time":ts,"date":parse_day(ts),
                "funding_interval_hours":None,
                "last_funding_rate":float(r[h[rate_col]])
            })
        archive_meta.append({
            "kind":"funding","archive_frequency":"daily","symbol":symbol,"period":ds,
            "filename":name,"url":url,"source":source,
            "upstream_checksum_status":"unavailable_404",
            "sha256":local_sha,"row_count":len(rows)-1
        })
    events.sort(key=lambda x:x["calc_time"])
    for i in range(1,len(events)):
        delta=events[i]["calc_time"]-events[i-1]["calc_time"]
        if delta<=0:
            raise RuntimeError(f"{symbol}: non-increasing fundingTime")
        events[i]["funding_interval_hours"]=delta/3600000.0
    archive_meta.append({
        "kind":"funding_partial_summary","archive_frequency":"daily",
        "symbol":symbol,"period":"2026-09-01_to_2026-09-20",
        "source":"official Binance Vision daily funding archives",
        "upstream_checksum_status":"unavailable_for_partial_daily_files",
        "native_intervals_hours":sorted({x["funding_interval_hours"] for x in events[1:] if x["funding_interval_hours"] is not None})
    })
    return archive_meta, events

price_meta=[]
fund_meta=[]
price_dates={}
funding_events={}

jobs=[]
with ThreadPoolExecutor(max_workers=12) as ex:
    for s in SYMBOLS:
        for ym in MONTHS:
            jobs.append(ex.submit(load_price,s,"monthly",ym))
            jobs.append(ex.submit(load_funding_monthly,s,ym))
        for d in PARTIAL_DAYS:
            jobs.append(ex.submit(load_price,s,"daily",d))
        jobs.append(ex.submit(load_funding_partial_archive,s))
    for f in as_completed(jobs):
        meta,payload=f.result()
        if isinstance(meta,list):
            symbol=next(x["symbol"] for x in meta if x.get("kind")=="funding_partial_summary")
            fund_meta.extend(meta); funding_events.setdefault(symbol,[]).extend(payload)
        elif meta["kind"]=="price":
            price_meta.append(meta); price_dates.setdefault(meta["symbol"],set()).update(payload)
        else:
            fund_meta.append(meta); funding_events.setdefault(meta["symbol"],[]).extend(payload)

price_meta.sort(key=lambda x:(x["symbol"],x["period"],x["archive_frequency"]))
fund_meta.sort(key=lambda x:(x["symbol"],x["period"],x["archive_frequency"]))
common=sorted(set.intersection(*(set(price_dates[s]) for s in SYMBOLS)))
common=[d for d in common if START <= d <= END]
if not common or common[0]!=START or common[-1]!=END:
    raise RuntimeError(f"common holdout panel mismatch: start/end={common[0] if common else None},{common[-1] if common else None}")

for s in SYMBOLS:
    ev=sorted(e for e in funding_events[s] if START <= e["date"] <= END)
    if not ev:
        raise RuntimeError(f"{s}: no funding coverage inside holdout")
    funding_events[s]=ev

manifest={
 "experiment_id":"HARMONY-HOLDOUT-DATA-001",
 "protocol":"HARMONY-HOLDOUT-PROTOCOL-001",
 "selection_data_end":"2025-10-31",
 "holdout_start":START,"holdout_end":END,"symbols":SYMBOLS,
 "completed_months":[f"{y:04d}-{m:02d}" for y,m in MONTHS],
 "partial_month_days":{"start":"2026-09-01","end":"2026-09-20","count":20},
 "source_rule":"monthly archives for completed months; daily kline archives and daily funding archives for partial final month",
 "price_archives":price_meta,"funding_archives":fund_meta,
 "common_panel":{"start":common[0],"end":common[-1],"observations":len(common)},
 "native_funding_intervals_hours":{s:sorted({e["funding_interval_hours"] for e in funding_events[s]}) for s in SYMBOLS},
 "frontier_digest":"bfdbd2a4e3bd5fd393479ae93f821d13d8f23d455e6763a574ed664da80e4966",
 "holdout_outcomes_released":False
}
manifest_bytes=json.dumps(manifest,indent=2,sort_keys=True).encode()+b"\n"
(OUT/"holdout-manifest.json").write_bytes(manifest_bytes)
(OUT/"common-panel-dates.json").write_text(json.dumps(common,indent=2)+"\n")
(OUT/"funding-coverage.json").write_text(json.dumps({
 s:[{"date":e["date"],"calc_time":e["calc_time"],"funding_interval_hours":e["funding_interval_hours"],"last_funding_rate":e["last_funding_rate"]} for e in funding_events[s]]
 for s in SYMBOLS
},indent=2,sort_keys=True)+"\n")
print(json.dumps({
 "experiment_id":"HARMONY-HOLDOUT-DATA-001",
 "common_panel_start":common[0],"common_panel_end":common[-1],
 "common_panel_observations":len(common),
 "price_archives":len(price_meta),"funding_archives":len(fund_meta),
 "manifest_sha256":sha256(manifest_bytes)
},indent=2,sort_keys=True))
