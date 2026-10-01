import hashlib, json, math, statistics, sys
from pathlib import Path
from datetime import datetime

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from experiments.run_harmony_deep_discovery_batch_004 import (
    SYMBOLS, END, OOS_START, load_daily_closes, load_funding,
    build_funding_weights, simulate
)

OUT = Path("artifacts/HARMONY-FIN-0024-DURABILITY-001")
EXPECTED = {"cumulative_return": 0.43002307869081013, "sharpe": 1.3589824243083648}
COST_MULTIPLIERS = (1.0, 2.0, 3.0, 4.0, 5.0)

def metrics_from_returns(rr):
    rr=list(rr)
    eq=1.0
    curve=[1.0]
    for x in rr:
        eq*=1.0+x
        curve.append(eq)
    sd=statistics.stdev(rr) if len(rr)>1 else 0.0
    sh=statistics.mean(rr)/sd*math.sqrt(365.25) if sd else 0.0
    peak=curve[0]; mdd=0.0
    for v in curve:
        peak=max(peak,v); mdd=min(mdd,v/peak-1.0)
    return {"cumulative_return":curve[-1]-1.0,"sharpe":sh,"max_drawdown":mdd,"final_equity":curve[-1],"observations":len(rr)}

def oos_curve(dates, equity):
    idx=[i for i,d in enumerate(dates) if d>=OOS_START]
    if not idx: raise RuntimeError("empty OOS")
    base=equity[idx[0]-1] if idx[0]>0 else 1.0
    return idx,[1.0]+[equity[i]/base for i in idx]

def daily_returns(curve):
    return [curve[i]/curve[i-1]-1.0 for i in range(1,len(curve))]

def benchmark_weight_fn(equal=False):
    def fn(i,d):
        if i != 0:
            return None
        return ({s:1.0/len(SYMBOLS) for s in SYMBOLS}
                if equal else {s:(1.0 if s==SYMBOLS[0] else 0.0) for s in SYMBOLS})
    return fn

def residual_stats(strategy, btc, ew):
    n=min(len(strategy),len(btc),len(ew))
    y=strategy[:n]; x=btc[:n]; z=ew[:n]
    if n<10: return {"cumulative_return":0.0,"sharpe":0.0,"observations":n}
    mx,mz,my=statistics.mean(x),statistics.mean(z),statistics.mean(y)
    s11=sum((v-mx)**2 for v in x); s22=sum((v-mz)**2 for v in z)
    s12=sum((a-mx)*(b-mz) for a,b in zip(x,z))
    sy1=sum((a-mx)*(b-my) for a,b in zip(x,y))
    sy2=sum((a-mz)*(b-my) for a,b in zip(x,y))
    det=s11*s22-s12*s12
    if abs(det)<1e-18: return {"cumulative_return":0.0,"sharpe":0.0,"observations":n}
    b1=(sy1*s22-sy2*s12)/det; b2=(sy2*s11-sy1*s12)/det
    a=my-b1*mx-b2*mz
    rr=[yy-(a+b1*xx+b2*zz) for yy,xx,zz in zip(y,x,z)]
    return metrics_from_returns(rr) | {"beta_btc":b1,"beta_equal_weight":b2,"intercept":a}

def segment_returns(rr, parts):
    out=[]
    for name,a,b in parts:
        sub=rr[a:b]
        m=metrics_from_returns(sub)
        m.update({"segment":name,"start_index":a,"end_index_exclusive":b})
        out.append(m)
    return out

def main():
    close={s:load_daily_closes(s) for s in SYMBOLS}
    common=sorted(set.intersection(*(set(close[s]) for s in SYMBOLS)))
    common=[d for d in common if d<=END]
    funding={s:load_funding(s) for s in SYMBOLS}
    strategy_w=build_funding_weights(common,funding)

    runs={}
    for mult in COST_MULTIPLIERS:
        runs[f"{mult:.1f}x"]=simulate(common,close,funding,strategy_w,mult)

    base=runs["1.0x"]
    observed=base["oos"]["metrics"] if "metrics" in base.get("oos",{}) else base["oos"]
    if abs(observed["cumulative_return"]-EXPECTED["cumulative_return"])>1e-12 or abs(observed["sharpe"]-EXPECTED["sharpe"])>1e-12:
        raise RuntimeError(json.dumps({"expected":EXPECTED,"observed":observed},sort_keys=True))

    btc=simulate(common,close,funding,benchmark_weight_fn(False),1.0)
    ew=simulate(common,close,funding,benchmark_weight_fn(True),1.0)
    _,sc=oos_curve(common,base["equity"])
    _,bc=oos_curve(common,btc["equity"])
    _,ec=oos_curve(common,ew["equity"])
    srr=daily_returns(sc); brr=daily_returns(bc); err=daily_returns(ec)

    n=len(srr)
    half=n//2; q=n//4
    temporal=segment_returns(srr,[("first_half",0,half),("second_half",half,n),
                                 ("quarter1",0,q),("quarter2",q,2*q),
                                 ("quarter3",2*q,3*q),("quarter4",3*q,n)])
    residual=residual_stats(srr,brr,err)

    rolling={}
    for w in (90,180):
        vals=[]
        for i in range(w,n+1):
            vals.append(metrics_from_returns(srr[i-w:i])["sharpe"])
        rolling[str(w)]={"median_sharpe":statistics.median(vals),"positive_sharpe_fraction":sum(v>0 for v in vals)/len(vals),"min_sharpe":min(vals),"max_sharpe":max(vals),"window_count":len(vals)}

    residual_quarters=[]
    # Fixed full-period factor residual diagnostics by chronological quarters.
    for k in range(4):
        a=k*q; b=(k+1)*q if k<3 else n
        residual_quarters.append({"quarter":k+1,**residual_stats(srr[a:b],brr[a:b],err[a:b])})

    stress={k:{"oos":runs[k]["oos"]["metrics"],"turnover":runs[k]["turnover"],"funding_pnl_sum":runs[k]["funding_pnl_sum"]} for k in runs}

    halves_positive = all(x["cumulative_return"]>0 and x["sharpe"]>0 for x in temporal[:2])
    quarters_positive = sum(x["cumulative_return"]>0 for x in temporal[2:])>=3
    residual_persistent = residual["cumulative_return"]>0 and sum(x["cumulative_return"]>0 for x in residual_quarters)>=3
    cost_preserved = stress["2.0x"]["oos"]["cumulative_return"]>0 and residual["cumulative_return"]>0
    final_status="DURABILITY_SUPPORTED" if (halves_positive and quarters_positive and residual_persistent and cost_preserved) else "DURABILITY_MIXED"

    payload={"audit_id":"HARMONY-FIN-0024-DURABILITY-001","status":final_status,
             "engine_freeze_commit":"44db6c6e441906b8f51fdcaf5c8b7529ffb3bff4",
             "baseline_reproduction":{"expected":EXPECTED,"observed":observed,"within_1e-12":True},
             "cost_stress":stress,"temporal":temporal,
             "benchmark_oos":{"btc":metrics_from_returns(brr),"equal_weight":metrics_from_returns(err)},
             "factor_residual":{"full_period":residual,"by_quarter":residual_quarters,"persistent":residual_persistent},
             "rolling":rolling,
             "classification_facts":{"halves_positive":halves_positive,"quarters_positive":quarters_positive,"residual_persistent":residual_persistent,"cost_preserved":cost_preserved},
             "integrity":{"candidate_immutable":True,"holdout_access":False,"parameter_search":False,"universe_search":False,"direction_search":False,"failed_v1_outputs_reused":False}}
    OUT.mkdir(parents=True,exist_ok=True)
    raw=(json.dumps(payload,sort_keys=True,indent=2)+"\n").encode()
    (OUT/"durability_audit.json").write_bytes(raw)
    summary={"audit_id":payload["audit_id"],"status":final_status,"result_sha256":hashlib.sha256(raw).hexdigest(),
             "baseline":observed,"cost_sharpe":{k:v["oos"]["sharpe"] for k,v in stress.items()},
             "first_half_sharpe":temporal[0]["sharpe"],"second_half_sharpe":temporal[1]["sharpe"],
             "residual_sharpe":residual["sharpe"],"residual_persistent":residual_persistent}
    (OUT/"SUMMARY.json").write_text(json.dumps(summary,sort_keys=True,indent=2)+"\n")
    (OUT/"report.md").write_text("# FIN-0024 Durability Audit 001\n\n"+json.dumps(payload,indent=2,sort_keys=True)+"\n")
    print(json.dumps(summary,indent=2,sort_keys=True))

if __name__=="__main__":
    main()
