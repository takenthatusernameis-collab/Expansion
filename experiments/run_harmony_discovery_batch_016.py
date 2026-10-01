import csv, json, math, statistics, zipfile, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

S=["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
A=["btc","eth","ltc","xrp","bnb","bch","ada","doge"]
CMROOT=Path("data/cache/harmony_gateway_v2")
FROOT=Path("data/cache/binance/futures_um/deep_history_2019")
OUT=Path("artifacts/HARMONY-DISCOVERY-BATCH-016")
DISCOVERY_END="2024-05-21"; OOS_START="2024-05-22"; END="2025-10-31"
FEE=.0006; SLIP=.0005; CAP=2

def parse_zip(p):
    with zipfile.ZipFile(p) as z:
        ns=[n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(ns)!=1: raise RuntimeError(f"unexpected archive {p}")
        return list(csv.reader(z.read(ns[0]).decode("utf-8","replace").splitlines()))

def dfrom(ms):
    ts=int(ms); sec=ts/1_000_000 if ts>=100_000_000_000_000 else ts/1000
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
    if (dates[0],dates[-1],len(dates))!=("2020-07-10",END,1935): raise RuntimeError("unexpected fixed panel")
    return dates,px,funding

def load_cm():
    cm={a:{} for a in A}
    for a in A:
        p=CMROOT/"coinmetrics_public"/f"{a}.csv"
        if not p.exists(): raise RuntimeError(f"missing public Coin Metrics archive: {p}")
        with p.open(newline="",encoding="utf-8") as f:
            for r in csv.DictReader(f):
                d=r.get("time","")[:10]
                if not d: continue
                vals={}
                for k,v in r.items():
                    if k=="time" or v in (None,""): continue
                    try: vals[k]=float(v)
                    except (TypeError,ValueError): pass
                if vals: cm[a][d]=vals
    return cm

def prior_day(dates,i):
    return dates[i-1] if i>0 else None

def score(cid,signal_day,dates,cm):
    sd=datetime.fromisoformat(signal_day).date()
    prior=sorted(d for d in dates if d<signal_day)
    if not prior: return None
    ranked=[]
    for a in A:
        if cid=="FIN-0085":
            d=prior[-1]; active=cm[a].get(d,{}).get("AdrActCnt"); cap=cm[a].get(d,{}).get("CapMrktCurUSD")
            if active is None or cap is None or cap<=0: return None
            val=active/cap; ascending=False
        elif cid=="FIN-0086":
            d=prior[-1]; val=cm[a].get(d,{}).get("CapMVRVCur")
            if val is None: return None
            ascending=True
        elif cid=="FIN-0087":
            d1=prior[-1]; d0=prior[-31] if len(prior)>=31 else None
            if d0 is None: return None
            v1=cm[a].get(d1,{}).get("AdrActCnt"); v0=cm[a].get(d0,{}).get("AdrActCnt")
            if v1 is None or v0 is None or v0<=0: return None
            val=v1/v0-1.0; ascending=False
        elif cid=="FIN-0088":
            end=prior[-1]; start=(datetime.fromisoformat(end).date()-timedelta(days=6))
            vols=[]; cap=None
            for d in prior:
                dd=datetime.fromisoformat(d).date()
                if start<=dd<=datetime.fromisoformat(end).date():
                    v=cm[a].get(d,{}).get("volume_reported_spot_usd_1d")
                    if v is not None: vols.append(v)
            cap=cm[a].get(end,{}).get("CapMrktCurUSD")
            if len(vols)<5 or cap is None or cap<=0: return None
            val=sum(vols)/cap; ascending=True
        else:
            raise ValueError(cid)
        ranked.append((val,a))
    return ranked

def rank_weights(scores,ascending=True):
    scores=sorted(scores,key=lambda x:(x[0],x[1]))
    low=scores[:3]; high=scores[-3:]
    w={s:0.0 for s in S}
    if ascending:
        for _,a in low: w[S[A.index(a)]]=1/6
        for _,a in high: w[S[A.index(a)]]=-1/6
    else:
        for _,a in high: w[S[A.index(a)]]=1/6
        for _,a in low: w[S[A.index(a)]]=-1/6
    return w

def build_targets(cid,dates,cm):
    out={}; attempts=usable=0
    for d in dates:
        day=datetime.fromisoformat(d).date()
        if cid=="FIN-0087":
            # Fixed monthly cadence: first Monday of each calendar month only.
            if day.weekday()!=0 or day.day>7: continue
        else:
            if day.weekday()!=0: continue
        attempts+=1; z=score(cid,d,dates,cm)
        if z is not None:
            asc = cid in ("FIN-0086","FIN-0088")
            out[d]=rank_weights(z,asc); usable+=1
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
            eq*=max(0,1-(FEE+SLIP)*mult*delta); turnover+=delta/2; prev=t.copy()
        curve.append(eq)
    liq=sum(abs(v) for v in prev.values()); eq*=max(0,1-(FEE+SLIP)*mult*liq); curve[-1]=eq
    return curve,turnover

def metrics(curve):
    rr=[curve[i]/curve[i-1]-1 for i in range(1,len(curve))]
    sd=statistics.stdev(rr) if len(rr)>1 else 0
    sh=statistics.mean(rr)/sd*math.sqrt(365.25) if sd else 0
    peak=curve[0]; mdd=0
    for x in curve: peak=max(peak,x); mdd=min(mdd,x/peak-1)
    return {"cumulative_return":curve[-1]-1,"sharpe":sh,"max_drawdown":mdd,"observations":len(curve)}

def prefix(curve,dates,end):
    idx=[i for i,d in enumerate(dates) if d<=end]; base=curve[idx[0]-1] if idx and idx[0]>0 else 1
    return metrics([1]+[curve[i]/base for i in idx])

def oos(curve,dates):
    idx=[i for i,d in enumerate(dates) if d>=OOS_START]; base=curve[idx[0]-1]
    return metrics([1]+[curve[i]/base for i in idx])

def main():
    dates,px,funding=load_panel(); cm=load_cm()
    results={}; passed=[]
    for cid in ["FIN-0085","FIN-0086","FIN-0087","FIN-0088"]:
        targets,attempts,usable=build_targets(cid,dates,cm); cov=usable/attempts if attempts else 0
        if usable<40 or cov<.80:
            results[cid]={"status":"DATA_BLOCKED","attempts":attempts,"usable":usable,"coverage":cov}; continue
        runs={}
        for mult in (1.,2.):
            curve,turn=simulate(dates,px,funding,targets,mult)
            runs[f"{mult:.1f}x"]={"discovery":prefix(curve,dates,DISCOVERY_END),
                                   "turnover":turn,"rebalances":sum(d<=DISCOVERY_END for d in targets)}
        ok=runs["1.0x"]["discovery"]["cumulative_return"]>0 and runs["1.0x"]["discovery"]["sharpe"]>0 and runs["1.0x"]["rebalances"]>=20
        results[cid]={"status":"CHEAP_PASS" if ok else "CHEAP_FAIL","attempts":attempts,"usable":usable,"coverage":cov,"runs":runs}
        if ok: passed.append(cid)
    passed.sort(key=lambda c:(results[c]["runs"]["2.0x"]["discovery"]["sharpe"],c),reverse=True)
    selected=passed[:CAP]; deep={}
    for cid in selected:
        targets,_,_=build_targets(cid,dates,cm)
        curves={}
        for mult in (1.,2.,3.):
            curve,turn=simulate(dates,px,funding,targets,mult); curves[f"{mult:.1f}x"]={"oos":oos(curve,dates),"turnover":turn}
        curve,_=simulate(dates,px,funding,targets,1.)
        oi=[i for i,d in enumerate(dates) if d>=OOS_START]; base=curve[oi[0]-1]
        half=len(oi)//2
        first=metrics([1]+[curve[i]/base for i in oi[:half]])
        second_base=curve[oi[half]-1]
        second=metrics([1]+[curve[i]/second_base for i in oi[half:]])
        deep[cid]={"runs":curves,"oos_halves":{"first":first,"second":second}}
    result={"batch_id":"HARMONY-DISCOVERY-BATCH-016","results":results,"passed_cheap":passed,"selected_for_deep":selected,"deep":deep,
            "integrity":{"holdout_access":False,"parameter_search":False,"universe_search":False,"direction_search":False,"candidate_mutation":False},
            "source_commit":"f1a36afb962731c387bb03982758ab0103063da5"}
    OUT.mkdir(parents=True,exist_ok=True)
    raw=(json.dumps(result,sort_keys=True,indent=2)+"\n").encode()
    (OUT/"HARMONY-DISCOVERY-BATCH-016-RESULT.json").write_bytes(raw)
    (OUT/"SUMMARY.json").write_text(json.dumps({"batch_id":result["batch_id"],"passed_cheap":passed,"selected_for_deep":selected,
        "result_sha256":hashlib.sha256(raw).hexdigest()},sort_keys=True,indent=2)+"\n")
    print(json.dumps({"passed_cheap":passed,"selected_for_deep":selected,"result_sha256":hashlib.sha256(raw).hexdigest()},indent=2))

if __name__=="__main__": main()
