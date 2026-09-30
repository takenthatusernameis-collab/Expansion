import csv, hashlib, json, math, statistics, zipfile
from datetime import datetime, timezone
from pathlib import Path

S = ["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
ROOT = Path("data/cache/binance/futures_um/monthly")
START = "2021-01-01"
END = "2025-10-31"
FEE = 0.0006
SLIP = 0.0005
REBALANCE_EVERY = 7
OUT = Path("artifacts/HARMONY-CAMPAIGN-001")
OUT.mkdir(parents=True, exist_ok=True)

CANDIDATES = [
    {
        "candidate_id":"funding_carry_mean_1d_weekly_v1",
        "lookback_days":1,
        "score":"negative_mean_native_funding",
        "feature":"mean_native_funding",
    },
    {
        "candidate_id":"funding_carry_mean_3d_weekly_v1",
        "lookback_days":3,
        "score":"negative_mean_native_funding",
        "feature":"mean_native_funding",
    },
    {
        "candidate_id":"funding_carry_mean_7d_weekly_v1",
        "lookback_days":7,
        "score":"negative_mean_native_funding",
        "feature":"mean_native_funding",
    },
    {
        "candidate_id":"funding_carry_zscore_7d_weekly_v1",
        "lookback_days":7,
        "score":"negative_prior_7d_mean_funding_over_prior_30d_funding_vol",
        "feature":"funding_zscore",
    },
]
CANDIDATE_DIGEST = hashlib.sha256(
    json.dumps(CANDIDATES,sort_keys=True,separators=(",",":")).encode()
).hexdigest()

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
        if len(names)!=1: raise RuntimeError(f"unexpected archive members: {path}")
        return list(csv.reader(io.StringIO(z.read(names[0]).decode("utf-8"))))

# stdlib import kept local to make this script dependency-light.
import io

px={s:{} for s in S}
fund={s:[] for s in S}
input_files=[]

for s in S:
    for y,m in months():
        kp=ROOT/"klines"/s/"1d"/f"{s}-1d-{y:04d}-{m:02d}.zip"
        fp=ROOT/"fundingRate"/s/f"{s}-fundingRate-{y:04d}-{m:02d}.zip"
        for p in (kp,fp):
            if not p.is_file(): raise FileNotFoundError(p)
            b=p.read_bytes()
            input_files.append({"path":str(p),"bytes":len(b),"sha256":hashlib.sha256(b).hexdigest()})
        for r in rows(kp):
            if r and r[0].isdigit():
                px[s][day(int(r[0]))]=float(r[4])
        rr=rows(fp)
        h={k.strip():i for i,k in enumerate(rr[0])}
        for required in ("calc_time","funding_interval_hours","last_funding_rate"):
            if required not in h: raise RuntimeError(f"{fp}: missing {required}")
        for r in rr[1:]:
            if r:
                fund[s].append((int(r[h["calc_time"]]),int(float(r[h["funding_interval_hours"]])),float(r[h["last_funding_rate"]])))

dates=sorted(set.intersection(*(set(px[s]) for s in S)))
if dates[0]!=START or dates[-1]!=END:
    raise RuntimeError(f"unexpected common price panel: {dates[0]}..{dates[-1]}")
assert len(dates)==1760

funding={s:{} for s in S}
for s in S:
    for ts,interval,rate in fund[s]:
        funding[s].setdefault(day(ts),[]).append((ts,interval,rate))
    for d in funding[s]:
        funding[s][d].sort()

# Precompute date-indexed historical native funding features once for every symbol.
date_to_idx={d:i for i,d in enumerate(dates)}
fund_rate_by_day={s:{} for s in S}
for s in S:
    for d,events in funding[s].items():
        fund_rate_by_day[s][d]=[rate for _,_,rate in events]

feature_cache={}
for d in dates:
    i=date_to_idx[d]
    for s in S:
        # Strictly prior observations: never include funding from decision date.
        prior_days=dates[max(0,i-30):i]
        seq=[r for dd in prior_days for r in fund_rate_by_day[s].get(dd,[])]
        feature_cache[(s,d)] = {
            "mean_1d": statistics.mean([r for dd in dates[max(0,i-1):i] for r in fund_rate_by_day[s].get(dd,[])]) if any(fund_rate_by_day[s].get(dd) for dd in dates[max(0,i-1):i]) else None,
            "mean_3d": statistics.mean([r for dd in dates[max(0,i-3):i] for r in fund_rate_by_day[s].get(dd,[])]) if any(fund_rate_by_day[s].get(dd) for dd in dates[max(0,i-3):i]) else None,
            "mean_7d": statistics.mean([r for dd in dates[max(0,i-7):i] for r in fund_rate_by_day[s].get(dd,[])]) if any(fund_rate_by_day[s].get(dd) for dd in dates[max(0,i-7):i]) else None,
            "mean_30d": statistics.mean(seq) if seq else None,
            "std_30d": statistics.pstdev(seq) if len(seq)>1 else None,
        }

def weights(candidate_id, d):
    lookback=int(next(x for x in CANDIDATES if x["candidate_id"]==candidate_id)["lookback_days"])
    scores=[]
    for s in S:
        f=feature_cache[(s,d)]
        if candidate_id=="funding_carry_mean_1d_weekly_v1":
            score=-f["mean_1d"] if f["mean_1d"] is not None else None
        elif candidate_id=="funding_carry_mean_3d_weekly_v1":
            score=-f["mean_3d"] if f["mean_3d"] is not None else None
        elif candidate_id=="funding_carry_mean_7d_weekly_v1":
            score=-f["mean_7d"] if f["mean_7d"] is not None else None
        else:
            if f["mean_7d"] is None or f["std_30d"] in (None,0):
                score=None
            else:
                score=-(f["mean_7d"]/f["std_30d"])
        if score is None:
            return None
        scores.append((score,s))
    scores.sort(key=lambda x:(-x[0],x[1]))
    w={s:0.0 for s in S}
    for _,s in scores[:2]: w[s]=0.25
    for _,s in scores[-2:]: w[s]=-0.25
    return w

def metrics(curve):
    rr=[curve[i]/curve[i-1]-1 for i in range(1,len(curve))]
    sd=statistics.stdev(rr) if len(rr)>1 else 0
    sharpe=(statistics.mean(rr)/sd)*math.sqrt(365.25) if sd else 0
    peak=curve[0]; mdd=0
    for x in curve:
        peak=max(peak,x); mdd=min(mdd,x/peak-1)
    cagr=curve[-1]**(365.25/max(1,len(curve)-1))-1
    return {"final_equity":curve[-1],"cumulative_return":curve[-1]-1,"cagr":cagr,"max_drawdown":mdd,"sharpe":sharpe,"observations":len(curve)}

def simulate(candidate_id, mode="strategy", cost_mult=1.0):
    eq=1.0; prev={s:0.0 for s in S}; curve=[]; turnover=0; cost_sum=0; funding_sum=0; funding_events=0
    first_valid=None
    for i,d in enumerate(dates):
        for s in S:
            for _,rate in funding[s].get(d,[]):
                eq*=1-prev[s]*rate
                funding_sum += -prev[s]*rate
                funding_events += 1
        if i:
            pd=dates[i-1]
            eq*=1+sum(prev[s]*(px[s][d]/px[s][pd]-1) for s in S)
        rebalance=(i%REBALANCE_EVERY==0)
        if rebalance:
            if mode=="strategy":
                tgt=weights(candidate_id,d)
                if tgt is None:
                    tgt=prev.copy()
                elif first_valid is None:
                    first_valid=d
            elif mode=="equal":
                tgt={s:1/len(S) for s in S}
            elif mode=="btc":
                tgt={s:(1.0 if s=="BTCUSDT" else 0.0) for s in S}
            else:
                raise ValueError(mode)
        else:
            tgt=prev.copy()
        delta=sum(abs(tgt[s]-prev[s]) for s in S)
        turnover += delta/2
        c=(FEE+SLIP)*cost_mult*delta
        eq*=1-c
        cost_sum += c
        prev=tgt
        curve.append(eq)
    liquidation=sum(abs(x) for x in prev.values())
    c=(FEE+SLIP)*cost_mult*liquidation
    eq*=1-c; cost_sum+=c; curve[-1]=eq
    return {
        "metrics":metrics(curve),
        "one_way_turnover":turnover,
        "transaction_cost_fraction":cost_sum,
        "funding_pnl_sum":funding_sum,
        "funding_events":funding_events,
        "first_valid_signal_date":first_valid,
    }

input_files.sort(key=lambda x:x["path"])
input_manifest={
    "dataset_id":"HARMONY-USDM-DAILY-FUNDING-2021-01-2025-10",
    "selection_data_end":END,
    "symbols":S,
    "observations":len(dates),
    "files":input_files,
}
input_manifest_bytes=json.dumps(input_manifest,sort_keys=True,indent=2).encode()+b"\n"
input_manifest_sha=hashlib.sha256(input_manifest_bytes).hexdigest()
(OUT/"input-manifest.json").write_bytes(input_manifest_bytes)
(OUT/"feature-cache-meta.json").write_text(json.dumps({
    "feature_set":"funding_carry_v1",
    "feature_cache_scope":"all_symbols_all_common_dates",
    "lookbacks_days":[1,3,7,30],
    "strict_prior_only":True,
    "candidate_digest":CANDIDATE_DIGEST,
    "input_manifest_sha256":input_manifest_sha
},sort_keys=True,indent=2)+"\n")

all_results={}
for candidate in CANDIDATES:
    cid=candidate["candidate_id"]
    base=simulate(cid,"strategy",1.0)
    stresses={f"{m:.1f}x":simulate(cid,"strategy",m)["metrics"] for m in (1,1.5,2)}
    result={
        "campaign_id":"HARMONY-CAMPAIGN-001",
        "candidate_id":cid,
        "candidate_scope_digest":CANDIDATE_DIGEST,
        "input_manifest_sha256":input_manifest_sha,
        "discovery_window":{"start":START,"end":END,"observations":len(dates)},
        "strategy":base,
        "benchmarks":{
            "same_universe_equal_weight_long_only":simulate(cid,"equal",1.0),
            "BTCUSDT_buy_and_hold":simulate(cid,"btc",1.0),
        },
        "cost_stress":stresses,
        "integrity":{
            "strict_prior_only_funding_features":True,
            "shared_feature_cache":True,
            "no_parameter_search":True,
            "no_universe_search":True,
            "holdout_access":False,
            "candidate_definition_mutated":False
        }
    }
    rb=json.dumps(result,sort_keys=True,indent=2).encode()+b"\n"
    result_sha=hashlib.sha256(rb).hexdigest()
    (OUT/f"{cid}.json").write_bytes(rb)
    all_results[cid]={**result,"result_sha256":result_sha}

summary={
    "campaign_id":"HARMONY-CAMPAIGN-001",
    "candidate_scope_digest":CANDIDATE_DIGEST,
    "input_manifest_sha256":input_manifest_sha,
    "discovery_window":{"start":START,"end":END,"observations":len(dates)},
    "candidates":{cid:{
        "result_sha256":r["result_sha256"],
        "cumulative_return":r["strategy"]["metrics"]["cumulative_return"],
        "cagr":r["strategy"]["metrics"]["cagr"],
        "sharpe":r["strategy"]["metrics"]["sharpe"],
        "max_drawdown":r["strategy"]["metrics"]["max_drawdown"],
        "turnover":r["strategy"]["one_way_turnover"],
    } for cid,r in all_results.items()},
    "holdout_access":False
}
(OUT/"summary.json").write_text(json.dumps(summary,sort_keys=True,indent=2)+"\n")
print(json.dumps(summary,sort_keys=True,indent=2))
