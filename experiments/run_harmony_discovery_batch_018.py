import csv, json, math, statistics, zipfile, hashlib
from datetime import datetime, timezone
from pathlib import Path

S=["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
A=["btc","eth","ltc","xrp","bnb","bch","ada","doge"]
CMROOT=Path("data/cache/harmony_gateway_v3")
FROOT=Path("data/cache/binance/futures_um/deep_history_2019")
OUT=Path("artifacts/HARMONY-DISCOVERY-BATCH-018")
DISCOVERY_END="2024-05-21"; OOS_START="2024-05-22"; END="2025-10-31"
FEE=.0006; SLIP=.0005; CAP=2

def parse_zip(p):
    with zipfile.ZipFile(p) as z:
        ns=[n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(ns)!=1: raise RuntimeError(f"unexpected archive {p}")
        return list(csv.reader(z.read(ns[0]).decode("utf-8","replace").splitlines()))

def dfrom(ms):
    ts=int(ms)
    sec=ts/1_000_000 if ts>=100_000_000_000_000 else ts/1000
    return datetime.fromtimestamp(sec,timezone.utc).date().isoformat()

def load_panel():
    px={s:{} for s in S}; funding={s:{} for s in S}
    for s in S:
        for p in sorted((FROOT/"klines"/s/"1d").glob(f"{s}-1d-*.zip")):
            for r in parse_zip(p):
                if r and r[0].isdigit():
                    d=dfrom(r[0])
                    if d<=END: px[s][d]=float(r[4])
        for r in json.loads((FROOT/"funding_gateway"/f"{s}-2019-2025-10.json").read_text()):
            d=dfrom(r["fundingTime"])
            if d<=END: funding[s].setdefault(d,[]).append(float(r["fundingRate"]))
    dates=sorted(set.intersection(*(set(px[s]) for s in S)))
    if (dates[0],dates[-1],len(dates))!=("2020-07-10",END,1935):
        raise RuntimeError(f"unexpected fixed futures panel: {dates[:1]}..{dates[-1:]} n={len(dates)}")
    return dates,px,funding

def load_activity():
    activity={a:{} for a in A}
    for a in A:
        p=CMROOT/"coinmetrics_public"/f"{a}.csv"
        if not p.exists(): raise RuntimeError(f"missing Coin Metrics archive: {p}")
        with p.open(newline="",encoding="utf-8") as f:
            for row in csv.DictReader(f):
                d=row.get("time","")[:10]
                if not d: continue
                vals={}
                for k in ("TxCnt","TxTfrCnt"):
                    v=row.get(k)
                    if v not in (None,""):
                        try: vals[k]=float(v)
                        except (TypeError,ValueError): pass
                if vals: activity[a][d]=vals
    return activity

def monday(d):
    return datetime.fromisoformat(d).date().weekday()==0

def score(cid,signal_day,dates,activity):
    prior=[d for d in dates if d<signal_day]
    if len(prior)<14: return None
    last7=prior[-7:]; prev7=prior[-14:-7]
    ranked=[]
    field="TxCnt" if cid=="FIN-0091" else "TxTfrCnt"
    for a,s in zip(A,S):
        if any(d not in activity[a] or field not in activity[a][d] for d in last7+prev7):
            return None
        x=sum(activity[a][d][field] for d in last7)
        y=sum(activity[a][d][field] for d in prev7)
        val=math.log((x+1.0)/(y+1.0))
        ranked.append((val,s))
    return ranked

def rank_weights(scores):
    scores=sorted(scores,key=lambda x:(x[0],x[1]))
    w={s:0.0 for s in S}
    for _,s in scores[:3]: w[s]=-1/6
    for _,s in scores[-3:]: w[s]=1/6
    return w

def build_targets(cid,dates,activity):
    out={}; attempts=usable=0
    for d in dates:
        if not monday(d): continue
        attempts+=1
        z=score(cid,d,dates,activity)
        if z is not None:
            out[d]=rank_weights(z); usable+=1
    return out,attempts,usable

def simulate(dates,px,funding,targets,mult):
    eq=1.0; prev={s:0.0 for s in S}; curve=[]; turnover=0.0
    for i,d in enumerate(dates):
        for s in S:
            for rate in funding[s].get(d,[]): eq*=1-prev[s]*rate
        if i:
            pd=dates[i-1]
            eq*=1+sum(prev[s]*(px[s][d]/px[s][pd]-1.0) for s in S)
        if d in targets:
            t=targets[d]; delta=sum(abs(t[s]-prev[s]) for s in S)
            eq*=max(0.0,1-(FEE+SLIP)*mult*delta); turnover+=delta/2; prev=t.copy()
        curve.append(eq)
    liq=sum(abs(v) for v in prev.values()); eq*=max(0.0,1-(FEE+SLIP)*mult*liq); curve[-1]=eq
    return curve,turnover

def metrics(curve):
    rr=[curve[i]/curve[i-1]-1 for i in range(1,len(curve))]
    sd=statistics.stdev(rr) if len(rr)>1 else 0.0
    sh=statistics.mean(rr)/sd*math.sqrt(365.25) if sd else 0.0
    peak=curve[0]; mdd=0.0
    for x in curve:
        peak=max(peak,x); mdd=min(mdd,x/peak-1)
    return {"cumulative_return":curve[-1]-1,"sharpe":sh,"max_drawdown":mdd,"observations":len(curve)}

def prefix(curve,dates,end):
    idx=[i for i,d in enumerate(dates) if d<=end]
    base=curve[idx[0]-1] if idx and idx[0]>0 else 1.0
    return metrics([1]+[curve[i]/base for i in idx])

def oos(curve,dates):
    idx=[i for i,d in enumerate(dates) if d>=OOS_START]
    base=curve[idx[0]-1]
    return metrics([1]+[curve[i]/base for i in idx])

def bench(dates,px):
    idx=[i for i,d in enumerate(dates) if d>=OOS_START]; a=idx[0]
    btc=[px["BTCUSDT"][d]/px["BTCUSDT"][dates[a]] for d in dates[a:]]
    ew=[1.0]
    for i in range(a+1,len(dates)):
        pd=dates[i-1]
        ew.append(ew[-1]*(1+sum((px[s][dates[i]]/px[s][pd]-1)/8 for s in S)))
    return btc,ew

def residual_sharpe(sc,bc,ec):
    import numpy as np
    sr=np.array([sc[i]/sc[i-1]-1 for i in range(1,len(sc))])
    br=np.array([bc[i]/bc[i-1]-1 for i in range(1,len(bc))])
    er=np.array([ec[i]/ec[i-1]-1 for i in range(1,len(ec))])
    n=min(len(sr),len(br),len(er))
    if n<10: return 0.0
    X=np.column_stack([np.ones(n),br[:n],er[:n]])
    y=sr[:n]; r=y-X@np.linalg.lstsq(X,y,rcond=None)[0]
    sd=float(np.std(r,ddof=1))
    return float(np.mean(r)/sd*math.sqrt(365.25)) if sd else 0.0

def main():
    dates,px,funding=load_panel(); activity=load_activity()
    results={}; passed=[]
    for cid in ["FIN-0091","FIN-0092"]:
        targets,attempts,usable=build_targets(cid,dates,activity); coverage=usable/attempts if attempts else 0
        if usable<40 or coverage<.80:
            results[cid]={"status":"DATA_BLOCKED","attempts":attempts,"usable":usable,"coverage":coverage}
            continue
        runs={}
        for mult in (1.,2.):
            curve,turn=simulate(dates,px,funding,targets,mult)
            runs[f"{mult:.1f}x"]={"discovery":prefix(curve,dates,DISCOVERY_END),
                                   "turnover":turn,"rebalances":sum(d<=DISCOVERY_END for d in targets)}
        ok=(runs["1.0x"]["discovery"]["cumulative_return"]>0 and
            runs["1.0x"]["discovery"]["sharpe"]>0 and
            runs["1.0x"]["discovery"]["rebalances"]>=20)
        results[cid]={"status":"CHEAP_PASS" if ok else "CHEAP_FAIL",
                      "attempts":attempts,"usable":usable,"coverage":coverage,"runs":runs}
        if ok: passed.append(cid)
    passed.sort(key=lambda c:(results[c]["runs"]["2.0x"]["discovery"]["sharpe"],c),reverse=True)
    selected=passed[:CAP]; deep={}
    btc,ew=bench(dates,px)
    for cid in selected:
        targets,_,_=build_targets(cid,dates,activity); curves={}
        for mult in (1.,2.,3.):
            curve,turn=simulate(dates,px,funding,targets,mult)
            curves[f"{mult:.1f}x"]={"oos":oos(curve,dates),"turnover":turn}
        curve,_=simulate(dates,px,funding,targets,1.)
        oi=[i for i,d in enumerate(dates) if d>=OOS_START]; base=curve[oi[0]-1]
        sc=[curve[i]/base for i in oi]; bc=[x/btc[0] for x in btc]; ec=[x/ew[0] for x in ew]
        mid=len(oi)//2
        first_base=base
        first=metrics([1]+[curve[i]/first_base for i in oi[:mid]])
        second=metrics([1]+[curve[i]/curve[oi[mid]-1] for i in oi[mid:]])
        deep[cid]={"runs":curves,
                   "oos_halves":{"first":first,"second":second},
                   "benchmark_oos":{"btc_cumulative_return":bc[-1]-1,
                                    "equal_weight_cumulative_return":ec[-1]-1},
                   "residual_sharpe_vs_btc_and_equal_weight":residual_sharpe(sc,bc,ec)}
    result={"batch_id":"HARMONY-DISCOVERY-BATCH-018","results":results,"passed_cheap":passed,
            "selected_for_deep":selected,"deep":deep,
            "integrity":{"holdout_access":False,"parameter_search":False,"universe_search":False,
                         "direction_search":False,"candidate_mutation":False},
            "source_commit":"f1a36afb962731c387bb03982758ab0103063da5"}
    OUT.mkdir(parents=True,exist_ok=True)
    raw=(json.dumps(result,sort_keys=True,indent=2)+"\n").encode()
    (OUT/"HARMONY-DISCOVERY-BATCH-018-RESULT.json").write_bytes(raw)
    (OUT/"SUMMARY.json").write_text(json.dumps({"batch_id":result["batch_id"],"passed_cheap":passed,
        "selected_for_deep":selected,"result_sha256":hashlib.sha256(raw).hexdigest()},
        sort_keys=True,indent=2)+"\n")
    print(json.dumps({"passed_cheap":passed,"selected_for_deep":selected,
                      "result_sha256":hashlib.sha256(raw).hexdigest()},indent=2))

if __name__=="__main__":
    main()
