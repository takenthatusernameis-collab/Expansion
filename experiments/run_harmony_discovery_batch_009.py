import csv, hashlib, json, math, statistics
from datetime import datetime, timezone
from pathlib import Path

SYMBOLS=["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
ROOT=Path("data/cache/binance/futures_um/deep_history_2019")
OUT=Path("artifacts/HARMONY-DISCOVERY-BATCH-009")
END="2025-10-31"; DISCOVERY_END="2024-05-21"; OOS_START="2024-05-22"
FEE=0.0006; SLIP=0.0005; DEEP_CAPACITY=2
CANDIDATES=["HARMONY-FIN-0051","HARMONY-FIN-0052"]

def parse_zip(path):
    import zipfile
    with zipfile.ZipFile(path) as z:
        names=[n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(names)!=1: raise RuntimeError(f"unexpected archive members: {path}")
        return list(csv.reader(z.open(names[0]).read().decode("utf-8").splitlines()))

def load():
    px={s:{} for s in SYMBOLS}; funding={s:{} for s in SYMBOLS}
    for s in SYMBOLS:
        for p in sorted((ROOT/"klines"/s/"1d").glob(f"{s}-1d-*.zip")):
            for row in parse_zip(p):
                if row and row[0].isdigit():
                    d=datetime.fromtimestamp(int(row[0])/1000,timezone.utc).date().isoformat()
                    if d<=END: px[s][d]=float(row[4])
        fp=ROOT/"funding_gateway"/f"{s}-2019-2025-10.json"
        for row in json.loads(fp.read_text()):
            d=datetime.fromtimestamp(int(row["fundingTime"])/1000,timezone.utc).date().isoformat()
            if d<=END: funding[s].setdefault(d,[]).append(float(row["fundingRate"]))
    dates=sorted(set.intersection(*(set(px[s]) for s in SYMBOLS)))
    if (dates[0],dates[-1],len(dates))!=("2020-07-10",END,1935):
        raise RuntimeError(f"unexpected panel {dates[:1]}..{dates[-1:]} n={len(dates)}")
    return dates,px,funding

def rets(dates,px):
    return {s:{d:(0.0 if i==0 else px[s][d]/px[s][dates[i-1]]-1.0) for i,d in enumerate(dates)} for s in SYMBOLS}

def month_key(d): return d[:7]

def rank_weights(scores, long_low):
    # scores is (score,symbol)
    if set(s for _,s in scores)!=set(SYMBOLS) or len(scores)!=len(SYMBOLS):
        raise RuntimeError("ranking universe mismatch")
    ordered=sorted(scores,key=lambda x:(x[0],x[1]))
    w={s:0.0 for s in SYMBOLS}
    low=ordered[:3]; high=ordered[-3:]
    if long_low:
        longs=[s for _,s in low]; shorts=[s for _,s in high]
    else:
        longs=[s for _,s in high]; shorts=[s for _,s in low]
    for s in longs: w[s]=1/6
    for s in shorts: w[s]=-1/6
    if abs(sum(abs(w[s]) for s in SYMBOLS)-1.0)>1e-12: raise RuntimeError("gross exposure invariant")
    return w

def targets(cid,dates,r):
    out={}
    for i,d in enumerate(dates):
        if cid=="HARMONY-FIN-0051":
            if i<60 or (i>0 and month_key(dates[i-1])==month_key(d)): continue
            br=[r["BTCUSDT"][dates[j]] for j in range(i-60,i)]
            forvals=[]
            mb=sum(br)/len(br)
            vb=sum((b-mb)**2 for b in br)
            for s in SYMBOLS:
                ar=[r[s][dates[j]] for j in range(i-60,i)]
                ma=sum(ar)/len(ar)
                cov=sum((a-ma)*(b-mb) for a,b in zip(ar,br))
                beta=cov/vb if vb>0 else 0.0
                resid=[a-(ma+beta*(b-mb)) for a,b in zip(ar,br)]
                score=statistics.pstdev(resid)
                forvals.append((score,s))
            out[d]=rank_weights(forvals,long_low=False)
        elif cid=="HARMONY-FIN-0052":
            if i<2: continue
            # Decide at today's open-equivalent research timestamp from yesterday's completed return.
            scorevals=[(r[s][dates[i-1]],s) for s in SYMBOLS]
            out[d]=rank_weights(scorevals,long_low=True)
        else: raise ValueError(cid)
    return out

def simulate(dates,px,funding,tar,mult=1.0,end_date=None):
    end_idx=len(dates)-1 if end_date is None else max(i for i,d in enumerate(dates) if d<=end_date)
    eq=1.0; prev={s:0.0 for s in SYMBOLS}; curve=[]; turn=0; rebs=0; fpnl=0
    for i in range(end_idx+1):
        d=dates[i]
        for s in SYMBOLS:
            for rate in funding[s].get(d,[]):
                pnl=-prev[s]*rate; eq*=1+pnl; fpnl+=pnl
        if i>0:
            pd=dates[i-1]; eq*=1+sum(prev[s]*(px[s][d]/px[s][pd]-1) for s in SYMBOLS)
        if d in tar:
            t=tar[d]
            if set(t)!=set(SYMBOLS) or abs(sum(abs(t[s]) for s in SYMBOLS)-1.0)>1e-12:
                raise RuntimeError("invalid target contract")
            delta=sum(abs(t[s]-prev[s]) for s in SYMBOLS)
            eq*=max(0,1-(FEE+SLIP)*mult*delta); turn+=delta/2; rebs+=1; prev=t.copy()
        curve.append(eq)
    liq=sum(abs(v) for v in prev.values()); eq*=max(0,1-(FEE+SLIP)*mult*liq); curve[-1]=eq
    return curve,turn,rebs,fpnl


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    dates,px,funding=load(); r=rets(dates,px)

    # Stage 1: discovery-only. No candidate OOS is computed before selection.
    discovery={}
    for cid in CANDIDATES:
        tar=targets(cid,dates,r)
        runs={}
        for mult in (1.0,2.0):
            curve,turn,rebs,fpnl=simulate(dates,px,funding,tar,mult,end_date=DISCOVERY_END)
            runs[f"{mult:.1f}x"]={"discovery":seg(dates,curve,"2020-07-10",DISCOVERY_END),"turnover":turn,"rebalance_count":rebs,"funding_pnl_sum":fpnl}
        passed=runs["1.0x"]["discovery"]["cumulative_return"]>0 and runs["1.0x"]["discovery"]["sharpe"]>0 and runs["1.0x"]["rebalance_count"]>=20
        discovery[cid]={"passed_gate":passed,"runs":runs}

    passed=sorted(
        [c for c in CANDIDATES if discovery[c]["passed_gate"]],
        key=lambda c:(discovery[c]["runs"]["2.0x"]["discovery"]["sharpe"],c),
        reverse=True,
    )
    selected=passed[:DEEP_CAPACITY]

    # Stage 2: conditional deep execution only for selected candidates.
    deep={}
    for cid in selected:
        tar=targets(cid,dates,r)
        runs={}
        for mult in (1.0,2.0):
            curve,turn,rebs,fpnl=simulate(dates,px,funding,tar,mult)
            runs[f"{mult:.1f}x"]={
                "full":metrics(curve),
                "oos":seg(dates,curve,OOS_START),
                "oos_halves":halves(dates,curve,OOS_START),
                "turnover":turn,
                "rebalance_count":rebs,
                "funding_pnl_sum":fpnl,
            }
        deep[cid]={"runs":runs}

    # Benchmarks are calculated only for the conditional deep-readout stage.
    if selected:
        ew_tar={d:{s:1.0/len(SYMBOLS) for s in SYMBOLS} for d in dates}
        btc_tar={d:{s:(1.0 if s=="BTCUSDT" else 0.0) for s in SYMBOLS} for d in dates}
        bcurve,_,_,_=simulate(dates,px,funding,btc_tar,1.0)
        ecurve,_,_,_=simulate(dates,px,funding,ew_tar,1.0)
        benchmarks={
            "BTCUSDT_buy_and_hold":{"full":metrics(bcurve),"oos":seg(dates,bcurve,OOS_START),"oos_halves":halves(dates,bcurve,OOS_START)},
            "same_universe_equal_weight_long_only":{"full":metrics(ecurve),"oos":seg(dates,ecurve,OOS_START),"oos_halves":halves(dates,ecurve,OOS_START)},
        }
    else:
        benchmarks={}

    manifest={
        "batch_id":"HARMONY-DISCOVERY-BATCH-009",
        "cache_key":"harmony-binance-um-deep-history-2019-2025-10-v1-36777989764",
        "panel":{"start":dates[0],"end":dates[-1],"rows":len(dates),"symbols":SYMBOLS},
        "discovery_end":DISCOVERY_END,"oos_start":OOS_START,
        "candidates":CANDIDATES,
        "cheap_gate":{"min_rebalances":20,"min_cumulative_return":0.0,"min_sharpe":0.0},
        "deep_capacity":DEEP_CAPACITY,
        "selection_metric":"discovery_sharpe_2x_cost",
        "oos_computed_for_nonselected":False,
        "no_parameter_search":True,
        "holdout_access":False,
    }
    mraw=(json.dumps(manifest,sort_keys=True,indent=2)+"\n").encode()
    (OUT/"input-manifest.json").write_bytes(mraw)
    payload={
        "batch_id":"HARMONY-DISCOVERY-BATCH-009",
        "input_manifest_sha256":hashlib.sha256(mraw).hexdigest(),
        "cheap_screen":discovery,
        "passed_cheap":passed,
        "selected_for_deep":selected,
        "deep_results":deep,
        "benchmarks":benchmarks,
        "integrity":{"holdout_access":False,"parameter_search":False,"universe_search":False,"direction_search":False,"candidate_mutation":False,"oos_computed_for_nonselected":False},
    }
    raw=(json.dumps(payload,sort_keys=True,indent=2)+"\n").encode()
    rsha=hashlib.sha256(raw).hexdigest()
    (OUT/"HARMONY-DISCOVERY-BATCH-009-RESULT.json").write_bytes(raw)
    (OUT/"HARMONY-DISCOVERY-BATCH-009-SUMMARY.json").write_text(
        json.dumps({
            "batch_id":payload["batch_id"],
            "passed_cheap":passed,
            "selected_for_deep":selected,
            "result_sha256":rsha,
            "discovery_2x_cost_sharpe":{c:discovery[c]["runs"]["2.0x"]["discovery"]["sharpe"] for c in CANDIDATES},
        },sort_keys=True,indent=2)+"\n"
    )
    print(json.dumps({"passed_cheap":passed,"selected_for_deep":selected,"result_sha256":rsha},indent=2))

if __name__=="__main__": main()
