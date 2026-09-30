import csv
import hashlib
import json
import math
import statistics
import time
from datetime import datetime, timezone, date
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

SYMBOLS = ["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
FUT_ROOT = Path("data/cache/binance/futures_um/deep_history_2019")
OUT = Path("artifacts/HARMONY-DEEP-DISCOVERY-BATCH-004")
END = "2025-10-31"
OOS_START = "2024-05-22"
FEE = 0.0006
SLIP = 0.0005
FGI_URL = "https://api.alternative.me/fng/?limit=0"


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def fetch(url: str) -> bytes:
    last_error = None
    for attempt in range(4):
        try:
            req = Request(url, headers={"User-Agent": "Harmony/DEEP-DISCOVERY-BATCH-004"})
            with urlopen(req, timeout=120) as response:
                return response.read()
        except HTTPError as exc:
            if exc.code not in (502, 503, 504):
                raise
            last_error = exc
            if attempt < 3:
                time.sleep(2 ** attempt)
    raise last_error


def load_daily_closes(symbol: str):
    import csv
    import io
    import zipfile
    closes = {}
    base = FUT_ROOT / "klines" / symbol / "1d"
    for path in sorted(base.glob(f"{symbol}-1d-*.zip")):
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith((".csv",".txt"))]
            if len(names) != 1:
                raise ValueError(f"unexpected archive shape: {path}")
            raw = archive.read(names[0]).decode("utf-8", "replace")
            for row in csv.reader(io.StringIO(raw)):
                if not row or not row[0].isdigit():
                    continue
                ts = int(row[0])
                seconds = ts / 1_000_000.0 if ts >= 100_000_000_000_000 else ts / 1_000.0
                d = datetime.fromtimestamp(seconds, timezone.utc).date().isoformat()
                if d <= END:
                    closes[d] = float(row[4])
    return closes


def load_funding(symbol: str):
    path = FUT_ROOT / "funding_gateway" / f"{symbol}-2019-2025-10.json"
    rows = json.loads(path.read_text())
    out = {}
    for row in rows:
        d = datetime.fromtimestamp(int(row["fundingTime"]) / 1000.0, timezone.utc).date().isoformat()
        out.setdefault(d, []).append(float(row["fundingRate"]))
    return out


def load_fgi(raw: bytes):
    payload = json.loads(raw.decode("utf-8"))
    values = {}
    for row in payload.get("data", []):
        if row.get("timestamp") is None or row.get("value") is None:
            continue
        d = datetime.fromtimestamp(int(row["timestamp"]), timezone.utc).date().isoformat()
        values[d] = float(row["value"])
    return values


def metrics(curve):
    curve = list(curve)
    rr = [curve[i] / curve[i - 1] - 1.0 for i in range(1, len(curve))]
    sd = statistics.stdev(rr) if len(rr) > 1 else 0.0
    sharpe = statistics.mean(rr) / sd * math.sqrt(365.25) if sd else 0.0
    peak = curve[0]
    mdd = 0.0
    for value in curve:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    years = max((len(curve) - 1) / 365.25, 1e-12)
    return {"final_equity": curve[-1], "cumulative_return": curve[-1] - 1.0,
            "cagr": curve[-1] ** (1.0 / years) - 1.0, "sharpe": sharpe,
            "max_drawdown": mdd, "observations": len(curve)}


def segment_metrics(dates, equity, start_date):
    selected = [(i,d,e) for i,(d,e) in enumerate(zip(dates,equity)) if d >= start_date]
    if not selected:
        return None
    first = selected[0][0]
    base = 1.0 if first == 0 else equity[first - 1]
    curve = [1.0] + [e / base for _i,_d,e in selected]
    out = metrics(curve)
    out.update({"start": selected[0][1], "end": selected[-1][1], "observations": len(selected)})
    return out


def halves_metrics(dates, equity, start_date):
    selected = [(i,d,e) for i,(d,e) in enumerate(zip(dates,equity)) if d >= start_date]
    if len(selected) < 4:
        return None
    mid = len(selected) // 2
    parts = [selected[:mid], selected[mid:]]
    outputs = []
    for part in parts:
        first = part[0][0]
        base = 1.0 if first == 0 else equity[first - 1]
        curve = [1.0] + [e / base for _i,_d,e in part]
        out = metrics(curve)
        out.update({"start": part[0][1], "end": part[-1][1], "observations": len(part)})
        outputs.append(out)
    return {"first_half": outputs[0], "second_half": outputs[1],
            "split": {"first_half_end": outputs[0]["end"], "second_half_start": outputs[1]["start"]}}


def simulate(dates, close, funding, weight_fn, cost_mult=1.0):
    prev = {s: 0.0 for s in SYMBOLS}
    eq = 1.0
    turnover = 0.0
    funding_pnl = 0.0
    curve = []
    for i,d in enumerate(dates):
        for s in SYMBOLS:
            for rate in funding.get(s, {}).get(d, []):
                pnl = -prev[s] * rate
                eq *= 1.0 + pnl
                funding_pnl += pnl
        if i > 0:
            pd = dates[i - 1]
            eq *= 1.0 + sum(prev[s] * (close[s][d] / close[s][pd] - 1.0) for s in SYMBOLS)
        target = weight_fn(i,d)
        if target is not None:
            delta = sum(abs(target.get(s,0.0) - prev.get(s,0.0)) for s in SYMBOLS)
            turnover += delta / 2.0
            eq *= max(0.0, 1.0 - (FEE + SLIP) * cost_mult * delta)
            prev = {s: target.get(s,0.0) for s in SYMBOLS}
        curve.append(eq)
    liq = sum(abs(v) for v in prev.values())
    eq *= max(0.0, 1.0 - (FEE + SLIP) * cost_mult * liq)
    curve[-1] = eq
    return {"metrics":metrics(curve),"oos":segment_metrics(dates,curve,OOS_START),
            "oos_halves":halves_metrics(dates,curve,OOS_START),
            "turnover":turnover,"funding_pnl_sum":funding_pnl,"equity":curve}


def build_funding_weights(dates, funding):
    state = {s: [] for s in SYMBOLS}
    by_date = {}
    for s in SYMBOLS:
        events=[]
        for d, rates in funding.get(s,{}).items():
            if d in dates:
                for r in rates:
                    events.append((d,r))
        by_date[s]=sorted(events)
    def weights(i,d):
        if i < 1 or (i - 1) % 7 != 0:
            return None
        signal_day = dates[i-1]
        means=[]
        for s in SYMBOLS:
            obs=[r for dd,r in by_date[s] if dd < signal_day][-21:]
            if len(obs) < 21:
                return None
            means.append((sum(obs)/len(obs),s))
        means.sort(key=lambda x:(x[0],x[1]))
        w={s:0.0 for s in SYMBOLS}
        for _,s in means[:3]:
            w[s]=1.0/6.0
        for _,s in means[-3:]:
            w[s]=-1.0/6.0
        return w
    return weights


def beta(x,y):
    if len(x) != len(y) or len(x) < 2:
        return None
    mx=sum(x)/len(x); my=sum(y)/len(y)
    den=sum((v-mx)**2 for v in y)
    if den <= 0:
        return None
    return sum((a-mx)*(b-my) for a,b in zip(x,y)) / den


def build_sentiment_beta_weights(dates, close, fgi):
    returns={}
    for s in SYMBOLS:
        returns[s]={}
        ds=sorted(close[s])
        for j in range(1,len(ds)):
            returns[s][ds[j]]=close[s][ds[j]]/close[s][ds[j-1]]-1.0
    fgi_change={}
    fd=sorted(fgi)
    for j in range(1,len(fd)):
        fgi_change[fd[j]]=fgi[fd[j]]-fgi[fd[j-1]]
    def weights(i,d):
        if i < 1 or (i - 1) % 7 != 0:
            return None
        signal_dates=[x for x in dates[:i] if x in fgi_change][-60:]
        if len(signal_dates) < 60:
            return None
        ranked=[]
        for s in SYMBOLS:
            xs=[]; ys=[]
            for sd in signal_dates:
                if sd in returns[s]:
                    xs.append(returns[s][sd]); ys.append(fgi_change[sd])
            b=beta(xs,ys)
            if b is None:
                return None
            ranked.append((b,s))
        ranked.sort(key=lambda x:(x[0],x[1]))
        w={s:0.0 for s in SYMBOLS}
        for _,s in ranked[2:6]:
            w[s]=1.0/8.0
        for _,s in ranked[:2]:
            w[s]=-1.0/8.0
        for _,s in ranked[-2:]:
            w[s]=-1.0/8.0
        return w
    return weights


def candidate_result(dates,close,funding,weight_fn):
    runs={}
    for mult in (1.0,1.5,2.0):
        runs[f"{mult:.1f}x"]=simulate(dates,close,funding,weight_fn,mult)
    base=runs["1.0x"]
    return {"base":{k:v for k,v in base.items() if k!="equity"},
            "cost_stress":{k:{"metrics":v["metrics"],"oos":v["oos"],"oos_halves":v["oos_halves"]} for k,v in runs.items()}}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    close={s:load_daily_closes(s) for s in SYMBOLS}
    common=sorted(set.intersection(*(set(close[s]) for s in SYMBOLS)))
    common=[d for d in common if d <= END]
    if not common:
        raise RuntimeError("empty common futures panel")
    funding={s:load_funding(s) for s in SYMBOLS}
    fgi_raw=fetch(FGI_URL)
    fgi=load_fgi(fgi_raw)
    if len(fgi) < 1000:
        raise RuntimeError(f"insufficient FGI history: {len(fgi)}")
    funding_result=candidate_result(common,close,funding,build_funding_weights(common,funding))
    sentiment_result=candidate_result(common,close,funding,build_sentiment_beta_weights(common,close,fgi))

    bh=simulate(common, {SYMBOLS[0]:close[SYMBOLS[0]], **{s:close[s] for s in SYMBOLS[1:]}}, {s:funding[s] for s in SYMBOLS},
                 lambda i,d: ({SYMBOLS[0]:1.0} if i==0 else None), 1.0)
    equal=simulate(common,close,funding,lambda i,d: ({s:1.0/len(SYMBOLS) for s in SYMBOLS} if i==0 or (i>0 and (i-1)%7==0) else None),1.0)

    manifest={"batch_id":"HARMONY-DEEP-DISCOVERY-BATCH-004",
              "futures_cache_key":"harmony-binance-um-deep-history-2019-2025-10-v1-36777989764",
              "common_panel":{"start":common[0],"end":common[-1],"observations":len(common)},
              "fgi_source":FGI_URL,"fgi_snapshot_sha256":sha256_bytes(fgi_raw),
              "fgi_snapshot_bytes":len(fgi_raw),"fgi_observations":len(fgi),
              "oos_start":OOS_START,"selection_end":END,
              "github_sha":__import__("os").environ.get("GITHUB_SHA")}
    mb=json.dumps(manifest,sort_keys=True,indent=2).encode()+b"\n"
    (OUT/"input-manifest.json").write_bytes(mb)
    msha=sha256_bytes(mb)

    payload={"batch_id":"HARMONY-DEEP-DISCOVERY-BATCH-004","input_manifest_sha256":msha,
             "candidates":{"HARMONY-FIN-0024":funding_result,"HARMONY-FIN-0025":sentiment_result},
             "benchmarks":{
                 "BTCUSDT_buy_and_hold":{"metrics":bh["metrics"],"oos":bh["oos"],"oos_halves":bh["oos_halves"]},
                 "equal_weight_long_only":{"metrics":equal["metrics"],"oos":equal["oos"],"oos_halves":equal["oos_halves"]}},
             "signal_diagnostics":{"FGI_observations":len(fgi)},
             "integrity":{"holdout_access":False,"parameter_search":False,"universe_search":False,"candidate_mutation":False}}
    raw=json.dumps(payload,sort_keys=True,indent=2).encode()+b"\n"
    rsha=sha256_bytes(raw)
    (OUT/"HARMONY-DEEP-DISCOVERY-BATCH-004-RESULT.json").write_bytes(raw)
    (OUT/"HARMONY-DEEP-DISCOVERY-BATCH-004-SUMMARY.json").write_text(json.dumps(
        {"batch_id":payload["batch_id"],"result_sha256":rsha,
         "candidates":{k:v["base"] for k,v in payload["candidates"].items()},
         "benchmarks":payload["benchmarks"]},sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"batch_id":payload["batch_id"],"result_sha256":rsha},indent=2))


if __name__ == "__main__":
    main()
