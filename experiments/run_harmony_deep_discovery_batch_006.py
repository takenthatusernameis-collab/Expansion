import csv
import hashlib
import io
import json
import math
import statistics
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SYMBOLS = ["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
PAGES = {
    "BTCUSDT":"Bitcoin",
    "ETHUSDT":"Ethereum",
    "LTCUSDT":"Litecoin",
    "XRPUSDT":"XRP",
    "BNBUSDT":"BNB",
    "BCHUSDT":"Bitcoin_Cash",
    "ADAUSDT":"Cardano",
    "DOGEUSDT":"Dogecoin",
}
FUT_ROOT = Path("data/cache/binance/futures_um/deep_history_2019")
OUT = Path("artifacts/HARMONY-DEEP-DISCOVERY-BATCH-006")
END = "2025-10-31"
OOS_START = "2024-05-22"
FEE = 0.0006
SLIP = 0.0005

def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()

def parse_zip_rows(path):
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise RuntimeError(f"unexpected archive members: {path}")
        return list(csv.reader(z.open(names[0]).read().decode("utf-8").splitlines()))

def load_daily_panel():
    px = {s:{} for s in SYMBOLS}
    funding = {s:{} for s in SYMBOLS}
    for s in SYMBOLS:
        root = FUT_ROOT / "klines" / s / "1d"
        for path in sorted(root.glob(f"{s}-1d-*.zip")):
            for row in parse_zip_rows(path):
                if row and row[0].isdigit():
                    d = datetime.fromtimestamp(int(row[0])/1000.0, timezone.utc).date().isoformat()
                    if d <= END:
                        px[s][d] = float(row[4])
        fpath = FUT_ROOT / "funding_gateway" / f"{s}-2019-2025-10.json"
        for row in json.loads(fpath.read_text()):
            d = datetime.fromtimestamp(int(row["fundingTime"])/1000.0, timezone.utc).date().isoformat()
            if d <= END:
                funding[s].setdefault(d, []).append(float(row["fundingRate"]))
    dates = sorted(set.intersection(*(set(px[s]) for s in SYMBOLS)))
    if not dates or dates[0] != "2020-07-10" or dates[-1] != END:
        raise RuntimeError(f"unexpected common panel: {dates[:1]}..{dates[-1:]}")
    return dates, px, funding

def month_key(d):
    return d[:7]

def month_end_dates(dates):
    out = {}
    for d in dates:
        out[month_key(d)] = d
    return out

def month_returns(dates, px):
    ends = month_end_dates(dates)
    months = sorted(ends)
    returns = {s:{} for s in SYMBOLS}
    for s in SYMBOLS:
        prev = None
        for m in months:
            if prev is not None and prev in ends:
                returns[s][m] = px[s][ends[m]] / px[s][ends[prev]] - 1.0
            prev = m
    return months, ends, returns

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent":"Harmony/DEEP-DISCOVERY-BATCH-006"})
    raw = urllib.request.urlopen(req, timeout=120).read()
    return raw

def alfred_initial_value(obs_month):
    y, m = map(int, obs_month.split("-"))
    if m == 12:
        vy, vm = y + 1, 1
    else:
        vy, vm = y, m + 1
    vintage = f"{vy:04d}-{vm:02d}-20"
    url = (
        "https://alfred.stlouisfed.org/graph/alfredgraph.csv?"
        + urllib.parse.urlencode({"id":"LMNUF1M","vintage_date":vintage})
    )
    raw = fetch(url)
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8"))))
    if not rows or len(rows[0]) < 2:
        raise RuntimeError(f"bad ALFRED payload for {obs_month}")
    value = None
    for row in rows[1:]:
        if not row:
            continue
        od = row[0]
        if od.startswith(obs_month):
            try:
                v = float(row[-1])
            except ValueError:
                continue
            if math.isfinite(v):
                value = v
                break
    if value is None:
        raise RuntimeError(f"LMNUF1M initial-vintage value unavailable for {obs_month} at {vintage}")
    return raw, vintage, value

def build_initial_finu(months):
    factor = {}
    provenance = {}
    for m in months:
        y, mo = map(int, m.split("-"))
        vintage = f"{(y + (1 if mo == 12 else 0)):04d}-{(1 if mo == 12 else mo + 1):02d}-20"
        try:
            raw, actual_vintage, value = alfred_initial_value(m)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                provenance[m] = {
                    "vintage_date": vintage,
                    "status": "unavailable_at_preregistered_vintage",
                    "http_status": 404,
                }
                continue
            raise
        except RuntimeError as exc:
            if str(exc).startswith("LMNUF1M initial-vintage value unavailable"):
                provenance[m] = {
                    "vintage_date": vintage,
                    "status": "unavailable_at_preregistered_vintage",
                    "http_status": 200,
                    "reason": str(exc),
                }
                continue
            raise
        factor[m] = value
        provenance[m] = {
            "vintage_date": actual_vintage,
            "status": "available",
            "raw_sha256": sha256_bytes(raw),
        }
    return factor, provenance

def ols_beta(y, x):
    n = len(y)
    if n < 2:
        return None
    mx = sum(x)/n
    my = sum(y)/n
    var = sum((v-mx)**2 for v in x)
    if var == 0.0:
        return 0.0
    cov = sum((x[i]-mx)*(y[i]-my) for i in range(n))
    return cov/var

def build_finu_signals(months, returns, factor):
    signals = {}
    for idx in range(1, len(months)):
        signal_month = months[idx]
        prior = months[:idx]
        prior = [m for m in prior if m in factor and all(m in returns[s] for s in SYMBOLS)]
        if len(prior) < 12:
            continue
        prior = prior[-24:]
        betas = []
        for s in SYMBOLS:
            y = [returns[s][m] for m in prior]
            x = [factor[m] for m in prior]
            beta = ols_beta(y, x)
            if beta is not None:
                betas.append((beta, s))
        if len(betas) != len(SYMBOLS):
            continue
        betas.sort(key=lambda z:(z[0], z[1]))
        w = {s:0.0 for s in SYMBOLS}
        for _, s in betas[:3]:
            w[s] = 1.0/6.0
        for _, s in betas[-3:]:
            w[s] = -1.0/6.0
        signals[signal_month] = w
    return signals

def fetch_wikipedia_monthly(page):
    title = urllib.parse.quote(page, safe="")
    url = (
        "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
        f"en.wikipedia.org/all-access/user/{title}/monthly/20200701/20251101"
    )
    raw = fetch(url)
    payload = json.loads(raw.decode("utf-8"))
    items = payload.get("items")
    if not isinstance(items, list):
        raise RuntimeError(f"bad Wikimedia payload for {page}")
    out = {}
    for row in items:
        ts = row.get("timestamp","")
        if len(ts) < 6:
            continue
        m = ts[:4] + "-" + ts[4:6]
        views = row.get("views")
        if views is not None:
            out[m] = float(views)
    if len(out) < 50:
        raise RuntimeError(f"insufficient Wikimedia history for {page}: {len(out)}")
    return raw, out

def attention_ratio(series, month, months):
    idx = months.index(month)
    if idx < 4:
        return None
    prior = months[idx-4:idx-1]
    latest = months[idx-1]
    if latest not in series or any(m not in series for m in prior):
        return None
    baseline = sum(series[m] for m in prior)/3.0
    if baseline <= 0.0:
        return None
    return series[latest]/baseline

def build_attention_signals(months, pageviews):
    signals = {}
    for m in months:
        ratios = []
        rmap = {}
        for s in SYMBOLS:
            r = attention_ratio(pageviews[s], m, months)
            if r is not None:
                ratios.append((r,s))
                rmap[s] = r
        if len(ratios) != len(SYMBOLS):
            continue
        ratios.sort(key=lambda z:(-z[0], z[1]))
        w = {s:0.0 for s in SYMBOLS}
        for _, s in ratios[:3]:
            w[s] = 1.0/6.0
        for _, s in ratios[-3:]:
            w[s] = -1.0/6.0
        signals[m] = w
    return signals

def metrics(curve):
    rr = [curve[i]/curve[i-1]-1.0 for i in range(1,len(curve))]
    sd = statistics.stdev(rr) if len(rr) > 1 else 0.0
    sharpe = statistics.mean(rr)/sd*math.sqrt(365.25) if sd else 0.0
    peak = curve[0]
    mdd = 0.0
    for x in curve:
        peak = max(peak,x)
        mdd = min(mdd,x/peak-1.0)
    years = max((len(curve)-1)/365.25,1e-12)
    return {
        "final_equity":curve[-1],
        "cumulative_return":curve[-1]-1.0,
        "cagr":curve[-1]**(1.0/years)-1.0,
        "sharpe":sharpe,
        "max_drawdown":mdd,
        "observations":len(curve),
    }

def segment_metrics(dates, equity, start):
    selected=[(i,d,e) for i,(d,e) in enumerate(zip(dates,equity)) if d>=start]
    if not selected:
        raise RuntimeError("empty OOS segment")
    first=selected[0][0]
    base=equity[first-1] if first>0 else 1.0
    curve=[1.0]+[e/base for _,_,e in selected]
    out=metrics(curve)
    out["start"]=selected[0][1]
    out["end"]=selected[-1][1]
    return out

def half_metrics(dates,equity):
    idx=[i for i,d in enumerate(dates) if d>=OOS_START]
    mid=len(idx)//2
    def part(xs):
        first=xs[0]
        base=equity[first-1] if first>0 else 1.0
        c=[1.0]+[equity[i]/base for i in xs]
        out=metrics(c)
        out["start"]=dates[xs[0]]
        out["end"]=dates[xs[-1]]
        return out
    return {"first_half":part(idx[:mid]),"second_half":part(idx[mid:])}

def simulate(dates, px, funding, monthly_targets, cost_mult=1.0):
    eq=1.0
    prev={s:0.0 for s in SYMBOLS}
    curve=[]
    turnover=0.0
    funding_sum=0.0
    for i,d in enumerate(dates):
        m=month_key(d)
        for s in SYMBOLS:
            for rate in funding[s].get(d,[]):
                pnl=-prev[s]*rate
                eq*=1.0+pnl
                funding_sum+=pnl
        if i>0:
            pd=dates[i-1]
            eq*=1.0+sum(prev[s]*(px[s][d]/px[s][pd]-1.0) for s in SYMBOLS)
        if d == month_end_dates_cached.get(m):
            pass
        target=monthly_targets.get(m)
        if target is not None and (i == 0 or month_key(dates[i-1]) != m):
            delta=sum(abs(target.get(s,0.0)-prev[s]) for s in SYMBOLS)
            eq*=max(0.0,1.0-(FEE+SLIP)*cost_mult*delta)
            turnover += delta/2.0
            prev={s:target.get(s,0.0) for s in SYMBOLS}
        curve.append(eq)
    liquidation=sum(abs(v) for v in prev.values())
    eq*=max(0.0,1.0-(FEE+SLIP)*cost_mult*liquidation)
    curve[-1]=eq
    return {
        "metrics":metrics(curve),
        "oos":segment_metrics(dates,curve,OOS_START),
        "oos_halves":half_metrics(dates,curve),
        "turnover":turnover,
        "funding_pnl_sum":funding_sum,
        "equity":curve,
    }

def equal_weight_targets(months):
    return {m:{s:1.0/len(SYMBOLS) for s in SYMBOLS} for m in months}

def btc_targets(months):
    return {m:{"BTCUSDT":1.0} for m in months}

def main():
    global month_end_dates_cached
    OUT.mkdir(parents=True,exist_ok=True)
    dates,px,funding=load_daily_panel()
    months,month_end_dates_cached,monthly_returns=month_returns(dates,px)
    usable_months=[m for m in months if m >= "2023-02" and m <= END[:7]]

    factor, factor_prov=build_initial_finu(usable_months)
    finu_signals=build_finu_signals(usable_months,monthly_returns,factor)

    pageviews={}
    pageview_prov={}
    for s,page in PAGES.items():
        raw,series=fetch_wikipedia_monthly(page)
        pageviews[s]=series
        pageview_prov[s]={"page":page,"raw_sha256":sha256_bytes(raw),"rows":len(series)}
    attention_signals=build_attention_signals(usable_months,pageviews)

    candidates={
        "HARMONY-FIN-0032":finu_signals,
        "HARMONY-FIN-0033":attention_signals,
    }
    results={}
    for name,signals in candidates.items():
        stress={f"{m:.1f}x":simulate(dates,px,funding,signals,m) for m in (1.0,1.5,2.0)}
        base=stress["1.0x"]
        results[name]={
            "base":{k:v for k,v in base.items() if k!="equity"},
            "cost_stress":{k:{"metrics":v["metrics"],"oos":v["oos"]} for k,v in stress.items()},
            "signal_month_count":len(signals),
        }

    bh=simulate(dates,px,funding,btc_targets(usable_months),1.0)
    eq=simulate(dates,px,funding,equal_weight_targets(usable_months),1.0)
    results["BTCUSDT_buy_and_hold"]={"metrics":bh["metrics"],"oos":bh["oos"],"oos_halves":bh["oos_halves"]}
    results["same_universe_equal_weight_long_only"]={"metrics":eq["metrics"],"oos":eq["oos"],"oos_halves":eq["oos_halves"]}

    manifest={
        "batch_id":"HARMONY-DEEP-DISCOVERY-BATCH-006",
        "futures_cache_key":"harmony-binance-um-deep-history-2019-2025-10-v1-36777989764",
        "futures_panel":{"start":dates[0],"end":dates[-1],"rows":len(dates)},
        "finu":{"series_id":"LMNUF1M","initial_vintage_rule":"20th calendar day of following month","month_count":len(factor),"vintages":factor_prov},
        "wikipedia":pageview_prov,
    }
    manifest_raw=(json.dumps(manifest,sort_keys=True,indent=2)+"\n").encode()
    manifest_sha=sha256_bytes(manifest_raw)
    (OUT/"input-manifest.json").write_bytes(manifest_raw)

    payload={
        "batch_id":"HARMONY-DEEP-DISCOVERY-BATCH-006",
        "input_manifest_sha256":manifest_sha,
        "candidates":results,
        "integrity":{
            "holdout_access":False,
            "parameter_search":False,
            "direction_search":False,
            "universe_search":False,
            "candidate_mutation":False,
            "finu_point_in_time_vintage_reconstruction":True,
            "attention_signal_uses_completed_prior_month":True,
        },
    }
    raw=(json.dumps(payload,sort_keys=True,indent=2)+"\n").encode()
    result_sha=sha256_bytes(raw)
    (OUT/"HARMONY-DEEP-DISCOVERY-BATCH-006-RESULT.json").write_bytes(raw)
    (OUT/"HARMONY-DEEP-DISCOVERY-BATCH-006-SUMMARY.json").write_text(
        json.dumps({"batch_id":payload["batch_id"],"result_sha256":result_sha,
                    "candidates":{k:v.get("base",v) for k,v in results.items()}},
                   sort_keys=True,indent=2)+"\n"
    )
    print(json.dumps({"batch_id":payload["batch_id"],"result_sha256":result_sha},indent=2))

if __name__=="__main__":
    main()
