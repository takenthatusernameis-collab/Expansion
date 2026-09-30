import csv, hashlib, io, json, statistics, zipfile
from datetime import datetime, timezone
from pathlib import Path

from harmony_backtest.feature_store import FeatureRow, feature_store_manifest, write_feature_store

S=["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
ROOT=Path("data/cache/binance/futures_um/monthly")
OUT=Path("artifacts/HARMONY-FEATURE-STORE-001")
STORE=Path("data/cache/features/funding_carry_v1/features.jsonl.gz")
START="2021-01-01"
END="2025-10-31"


def day(ms):
    return datetime.fromtimestamp(ms/1000,timezone.utc).date().isoformat()


def months():
    out=[]; y,m=2021,1
    while (y,m)<=(2025,10):
        out.append((y,m)); m+=1
        if m==13:y,m=y+1,1
    return out


def rows(path):
    with zipfile.ZipFile(path) as z:
        names=[n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(names)!=1: raise RuntimeError(path)
        return list(csv.reader(io.StringIO(z.read(names[0]).decode("utf-8"))))


fund={s:{} for s in S}
for s in S:
    for y,m in months():
        p=ROOT/"fundingRate"/s/f"{s}-fundingRate-{y:04d}-{m:02d}.zip"
        if not p.is_file(): raise FileNotFoundError(p)
        rr=rows(p); h={k.strip():i for i,k in enumerate(rr[0])}
        for r in rr[1:]:
            if r:
                d=day(int(r[h["calc_time"]]))
                fund[s].setdefault(d,[]).append(float(r[h["last_funding_rate"]]))

# Use the common price-panel dates as the canonical decision grid.
px_dates=None
for s in S:
    dates=set()
    for y,m in months():
        p=ROOT/"klines"/s/"1d"/f"{s}-1d-{y:04d}-{m:02d}.zip"
        for r in rows(p):
            if r and r[0].isdigit(): dates.add(day(int(r[0])))
    px_dates=dates if px_dates is None else px_dates & dates
dates=sorted(d for d in px_dates if START<=d<=END)
if len(dates)!=1760 or dates[0]!=START or dates[-1]!=END: raise RuntimeError("unexpected common panel")

feature_rows=[]
for s in S:
    for i,d in enumerate(dates):
        prior_30=dates[max(0,i-30):i]
        vals=[r for dd in prior_30 for r in fund[s].get(dd,[])]
        def mean_days(n):
            seq=[r for dd in dates[max(0,i-n):i] for r in fund[s].get(dd,[])]
            return statistics.mean(seq) if seq else None
        feature_rows.append(FeatureRow(
            symbol=s,
            date=d,
            values={
                "mean_1d":mean_days(1),
                "mean_3d":mean_days(3),
                "mean_7d":mean_days(7),
                "mean_30d":statistics.mean(vals) if vals else None,
                "std_30d":statistics.pstdev(vals) if len(vals)>1 else None,
            },
        ))

OUT.mkdir(parents=True,exist_ok=True)
sha=write_feature_store(STORE,feature_rows)
manifest=feature_store_manifest(
    dataset_manifest_sha256="c5c3807d4a244b7c1b788fcc1bf84f444301794ee555af8f3f5d56de77b41f73",
    feature_set_id="funding_carry_v1",
    feature_revision="2026-09-30-r1",
    feature_store_sha256=sha,
    row_count=len(feature_rows),
)
(OUT/"feature-store-manifest.json").write_text(manifest)
print(json.dumps({
    "feature_set_id":"funding_carry_v1",
    "row_count":len(feature_rows),
    "feature_store_sha256":sha,
    "dataset_manifest_sha256":"c5c3807d4a244b7c1b788fcc1bf84f444301794ee555af8f3f5d56de77b41f73"
},sort_keys=True,indent=2))
