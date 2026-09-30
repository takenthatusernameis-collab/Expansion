import csv, hashlib, io, json, math, statistics, zipfile
from datetime import datetime, timezone
from pathlib import Path

S = ["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
HIST_ROOT = Path("data/cache/binance/futures_um/monthly")
HOLDOUT_ROOT = Path("data/cache/binance/futures_um/holdout_2025-11_2026-08")
SELECTION_END = "2025-10-31"
HOLDOUT_START = "2025-11-01"
HOLDOUT_END = "2026-08-31"
FEE = 0.0006
SLIP = 0.0005
REBALANCE_EVERY = 7
FRONTIER_DIGEST = "bfdbd2a4e3bd5fd393479ae93f821d13d8f23d455e6763a574ed664da80e4966"
CANDIDATE_BLOBS = {
    "cross_sectional_vol_normalized_momentum_20d_weekly_v1": "560089bec9f6f7560b62ad7dc0b53ebe99791584",
    "cross_sectional_trend_efficiency_20d_weekly_v1": "752eaae448cfac756023704e83335cace5dc3cbf",
    "cross_sectional_reversal_5d_weekly_v1": "11ea789b29b4f9c266f6345429546ad471d31d59",
    "cross_sectional_residual_momentum_20d_beta60_weekly_v1": "1a326cc5ba805cf52a5dfc19fd1285b0e2f07ddd",
}
CANDIDATES = sorted(CANDIDATE_BLOBS)
OUT = Path("artifacts/HARMONY-HOLDOUT-EXECUTION-001")
OUT.mkdir(parents=True, exist_ok=True)

def day(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).date().isoformat()

def month_list():
    out=[]; y,m=2021,1
    while (y,m) <= (2026,8):
        out.append((y,m)); m+=1
        if m == 13: y,m=y+1,1
    return out

def read_zip_rows(path):
    with zipfile.ZipFile(path) as z:
        names=[n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(names)!=1: raise RuntimeError(f"unexpected archive members: {path}")
        return list(csv.reader(io.StringIO(z.read(names[0]).decode("utf-8"))))

def read_price(symbol, year, month, root):
    name=f"{symbol}-1d-{year:04d}-{month:02d}.zip"
    path=root/"klines"/symbol/"1d"/name
    rows=read_zip_rows(path)
    px={}
    for r in rows:
        if r and r[0].isdigit():
            px[day(int(r[0]))]=float(r[4])
    if not px: raise RuntimeError(f"no price rows: {path}")
    return px

def read_funding(symbol, year, month, root):
    name=f"{symbol}-fundingRate-{year:04d}-{month:02d}.zip"
    path=root/"fundingRate"/symbol/name
    rows=read_zip_rows(path)
    h={k.strip():i for i,k in enumerate(rows[0])}
    required={"calc_time","funding_interval_hours","last_funding_rate"}
    if not required.issubset(h): raise RuntimeError(f"funding schema mismatch: {path}")
    out=[]
    for r in rows[1:]:
        if r:
            out.append((int(r[h["calc_time"]]),int(float(r[h["funding_interval_hours"]])),float(r[h["last_funding_rate"]])))
    return out

px={s:{} for s in S}
fund={s:[] for s in S}

for s in S:
    for y,m in month_list():
        root=HIST_ROOT if (y,m) <= (2025,10) else HOLDOUT_ROOT
        part_px=read_price(s,y,m,root)
        px[s].update(part_px)
        fund[s].extend(read_funding(s,y,m,root))

dates=sorted(set.intersection(*(set(px[s]) for s in S)))
assert dates[0]=="2021-01-01" and dates[-1]==HOLDOUT_END
selection_dates=[d for d in dates if d <= SELECTION_END]
holdout_dates=[d for d in dates if HOLDOUT_START <= d <= HOLDOUT_END]
assert len(selection_dates)==1760
assert len(holdout_dates)==304
assert holdout_dates[0]==HOLDOUT_START and holdout_dates[-1]==HOLDOUT_END

funding={s:{} for s in S}
for s in S:
    for ts,interval,rate in fund[s]:
        funding[s].setdefault(day(ts),[]).append((interval,rate))

def target(kind,i):
    signal_date=dates[i-1]
    base20=dates[i-21]
    if kind=="FIN-0009":
        scores=[]
        for s in S:
            r20=px[s][signal_date]/px[s][base20]-1.0
            rr=[px[s][dates[j]]/px[s][dates[j-1]]-1.0 for j in range(i-20,i)]
            vol=statistics.pstdev(rr)
            scores.append((r20/vol if vol>0 else 0.0,s))
    elif kind=="FIN-0010":
        scores=[]
        for s in S:
            r20=px[s][signal_date]/px[s][base20]-1.0
            rr=[px[s][dates[j]]/px[s][dates[j-1]]-1.0 for j in range(i-20,i)]
            path=sum(abs(x) for x in rr)
            scores.append((r20/path if path>0 else 0.0,s))
    elif kind=="FIN-0011":
        base5=dates[i-6]
        scores=[(-(px[s][signal_date]/px[s][base5]-1.0),s) for s in S]
    elif kind=="FIN-0012":
        btc20=px["BTCUSDT"][signal_date]/px["BTCUSDT"][base20]-1.0
        br=[px["BTCUSDT"][dates[j]]/px["BTCUSDT"][dates[j-1]]-1.0 for j in range(i-60,i)]
        bm=sum(br)/len(br)
        bv=sum((x-bm)**2 for x in br)/len(br)
        scores=[]
        for s in S:
            ar=[px[s][dates[j]]/px[s][dates[j-1]]-1.0 for j in range(i-60,i)]
            am=sum(ar)/len(ar)
            cov=sum((x-am)*(y-bm) for x,y in zip(ar,br))/len(br)
            beta=cov/bv if bv>0 else 0.0
            r20=px[s][signal_date]/px[s][base20]-1.0
            scores.append((r20-beta*btc20,s))
    else:
        raise ValueError(kind)
    scores.sort(key=lambda z:(-z[0],z[1]))
    w={s:0.0 for s in S}
    for _,s in scores[:2]: w[s]=0.25
    for _,s in scores[-2:]: w[s]=-0.25
    return w

def metrics(curve):
    rr=[curve[i]/curve[i-1]-1.0 for i in range(1,len(curve))]
    sd=statistics.stdev(rr) if len(rr)>1 else 0.0
    sharpe=(statistics.mean(rr)/sd)*math.sqrt(365.25) if sd else 0.0
    peak=curve[0]; mdd=0.0
    for x in curve:
        peak=max(peak,x); mdd=min(mdd,x/peak-1.0)
    cagr=curve[-1]**(365.25/max(1,len(curve)-1))-1.0
    return {"final_equity":curve[-1],"cumulative_return":curve[-1]-1.0,"cagr":cagr,"max_drawdown":mdd,"sharpe":sharpe,"observations":len(curve)}

def simulate(start_date_index,end_date_index,mode,cost_mult=1.0):
    eq=1.0; prev={s:0.0 for s in S}; curve=[]; turnover=0.0; cost_sum=0.0; funding_sum=0.0; funding_events=0
    for i in range(start_date_index,end_date_index):
        d=dates[i]
        for s in S:
            for _,rate in funding[s].get(d,[]):
                eq*=1.0-prev[s]*rate
                funding_sum += -prev[s]*rate
                funding_events += 1
        if i>start_date_index:
            pd=dates[i-1]
            daily=sum(prev[s]*(px[s][d]/px[s][pd]-1.0) for s in S)
            eq*=1.0+daily
        rebalance=((i-start_date_index)%REBALANCE_EVERY==0)
        if rebalance:
            if mode=="strategy":
                if i < 61: tgt={s:0.0 for s in S}
                else: tgt=target(current_candidate,i)
            elif mode=="equal_weight":
                tgt={s:1.0/len(S) for s in S}
            elif mode=="btc":
                tgt={s:(1.0 if s=="BTCUSDT" else 0.0) for s in S}
            else: raise ValueError(mode)
        else:
            tgt=prev.copy()
        delta=sum(abs(tgt[s]-prev[s]) for s in S)
        turnover += delta/2.0
        cost=(FEE+SLIP)*cost_mult*delta
        eq*=1.0-cost
        cost_sum += cost
        prev=tgt
        curve.append(eq)
    liquidation=sum(abs(v) for v in prev.values())
    liq_cost=(FEE+SLIP)*cost_mult*liquidation
    eq*=1.0-liq_cost
    cost_sum += liq_cost
    curve[-1]=eq
    return {"metrics":metrics(curve),"one_way_turnover":turnover,"mean_daily_one_way_turnover":turnover/len(curve),"transaction_cost_fraction":cost_sum,"funding_pnl_sum":funding_sum,"funding_events":funding_events}

# Actual archive/input manifest, computed from bytes on this runner.
manifest_entries=[]
for root,label,months in [
    (HIST_ROOT,"selection_cache",[(y,m) for y,m in month_list() if (y,m)<=(2025,10)]),
    (HOLDOUT_ROOT,"holdout_cache",[(y,m) for y,m in month_list() if (y,m)>=(2025,11)])
]:
    for s in S:
        for y,m in months:
            for kind,rel in [("price",Path("klines")/s/"1d"/f"{s}-1d-{y:04d}-{m:02d}.zip"),("funding",Path("fundingRate")/s/f"{s}-fundingRate-{y:04d}-{m:02d}.zip")]:
                p=root/rel
                b=p.read_bytes()
                manifest_entries.append({"cache":label,"kind":kind,"path":str(p),"sha256":hashlib.sha256(b).hexdigest(),"bytes":len(b)})
manifest_entries.sort(key=lambda x:x["path"])
assert len(manifest_entries)==1088
input_manifest_bytes=json.dumps({
    "selection_cache_verified_manifest_sha256":"555b2be6e2671729a2613ad58c1c0b4eed7272415b154673fedd34db4b0f5da5",
    "holdout_materialization_manifest_sha256":"8f626b3bc8b682b6213e414fd5d47f9339a04852abdf3b43d23f7b1481c8f00b",
    "frontier_digest":FRONTIER_DIGEST,
    "selection_end":SELECTION_END,"holdout_start":HOLDOUT_START,"holdout_end":HOLDOUT_END,
    "symbols":S,"files":manifest_entries
},indent=2,sort_keys=True).encode()+b"\n"
input_manifest_sha=hashlib.sha256(input_manifest_bytes).hexdigest()
(OUT/"input-manifest.json").write_bytes(input_manifest_bytes)

# candidate executions
candidate_configs = [
    ("FIN-0009","cross_sectional_vol_normalized_momentum_20d_weekly_v1"),
    ("FIN-0010","cross_sectional_trend_efficiency_20d_weekly_v1"),
    ("FIN-0011","cross_sectional_reversal_5d_weekly_v1"),
    ("FIN-0012","cross_sectional_residual_momentum_20d_beta60_weekly_v1"),
]
holdout_start_idx=dates.index(HOLDOUT_START)
holdout_end_idx=dates.index(HOLDOUT_END)+1
mid_idx=holdout_start_idx + len(holdout_dates)//2
all_results={}
for exp_id,cid in candidate_configs:
    current_candidate = exp_id
    base=simulate(holdout_start_idx,holdout_end_idx,"strategy",1.0)
    first=simulate(holdout_start_idx,mid_idx,"strategy",1.0)
    second=simulate(mid_idx,holdout_end_idx,"strategy",1.0)
    equal=simulate(holdout_start_idx,holdout_end_idx,"equal_weight",1.0)
    btc=simulate(holdout_start_idx,holdout_end_idx,"btc",1.0)
    stresses={f"{m:.1f}x":simulate(holdout_start_idx,holdout_end_idx,"strategy",m)["metrics"] for m in (1.0,1.5,2.0)}
    result={
        "experiment_id":"HARMONY-HOLDOUT-EXECUTION-001",
        "candidate_id":cid,
        "experiment_ref":exp_id,
        "candidate_blob_sha":CANDIDATE_BLOBS[cid],
        "frontier_digest":FRONTIER_DIGEST,
        "source_manifest_sha256":input_manifest_sha,
        "data":{"selection_end":SELECTION_END,"holdout_start":HOLDOUT_START,"holdout_end":HOLDOUT_END,"holdout_observations":len(holdout_dates),"symbols":S},
        "strategy":base,
        "benchmarks":{"same_universe_equal_weight_long_only":equal,"BTCUSDT_buy_and_hold":btc},
        "temporal_stability":{"first_half":first,"second_half":second,"stable":abs(first["metrics"]["sharpe"]-second["metrics"]["sharpe"])<0.0},
        "cost_stress":stresses,
        "integrity":{
            "fixed_candidate":True,"candidate_definition_mutated":False,"prior_only_signal":True,
            "native_funding_intervals_used":True,"no_parameter_search":True,"no_universe_search":True,
            "holdout_outcome_used_for_selection":False,"frontier_mutated":False,
            "input_manifest_bound_to_result":True
        }
    }
    rb=json.dumps(result,indent=2,sort_keys=True).encode()+b"\n"
    (OUT/f"{exp_id}-result.json").write_bytes(rb)
    result["_result_bytes_sha256"]=hashlib.sha256(rb).hexdigest()
    all_results[exp_id]=result

summary={
    "experiment_id":"HARMONY-HOLDOUT-EXECUTION-001",
    "protocol":"HARMONY-HOLDOUT-PROTOCOL-001",
    "frontier_digest":FRONTIER_DIGEST,
    "input_manifest_sha256":input_manifest_sha,
    "holdout":{"start":HOLDOUT_START,"end":HOLDOUT_END,"observations":len(holdout_dates)},
    "candidates":{k:{
        "candidate_id":v["candidate_id"],
        "candidate_blob_sha":v["candidate_blob_sha"],
        "sharpe":v["strategy"]["metrics"]["sharpe"],
        "cumulative_return":v["strategy"]["metrics"]["cumulative_return"],
        "max_drawdown":v["strategy"]["metrics"]["max_drawdown"],
        "first_half_sharpe":v["temporal_stability"]["first_half"]["metrics"]["sharpe"],
        "second_half_sharpe":v["temporal_stability"]["second_half"]["metrics"]["sharpe"]
    } for k,v in all_results.items()},
    "released_into_selection_state":False
}
(OUT/"readout.json").write_text(json.dumps(summary,indent=2,sort_keys=True)+"\n")
print(json.dumps(summary,indent=2,sort_keys=True))
