import csv,io,json,math,statistics,zipfile,hashlib
from pathlib import Path
from datetime import datetime,timezone,timedelta

S=["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
A=["btc","eth","ltc","xrp","bnb","bch","ada","doge"]
CMROOT=Path("data/cache/harmony_gateway_v3")
FROOT=Path("data/cache/binance/futures_um/deep_history_2019")
OUT=Path("artifacts/HARMONY-DISCOVERY-BATCH-015")
DISCOVERY_END="2024-05-21"; OOS_START="2024-05-22"; END="2025-10-31"
FEE=.0006; SLIP=.0005; CAP=2

def parse_zip(p):
    with zipfile.ZipFile(p) as z:
        ns=[n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(ns)!=1: raise RuntimeError(f"unexpected archive {p}")
        return list(csv.reader(io.StringIO(z.read(ns[0]).decode("utf-8","replace"))))

def dfrom(ms):
    ts=int(ms); sec=ts/1_000_000 if ts>=100_000_000_000_000 else ts/1000
    return datetime.fromtimestamp(sec,timezone.utc).date().isoformat()

def load_panel():
    fut={s:{} for s in S}; funding={s:{} for s in S}
    for s in S:
        for p in sorted((FROOT/"klines"/s/"1d").glob(f"{s}-1d-*.zip")):
            for r in parse_zip(p):
                if r and r[0].isdigit():
                    d=dfrom(r[0])
                    if d<=END: fut[s][d]=float(r[4])
        for r in json.loads((FROOT/"funding_gateway"/f"{s}-2019-2025-10.json").read_text()):
            d=dfrom(r["fundingTime"])
            if d<=END: funding[s].setdefault(d,[]).append(float(r["fundingRate"]))
    dates=sorted(set.intersection(*(set(fut[s]) for s in S)))
    if (dates[0],dates[-1],len(dates))!=("2020-07-10",END,1935):
        raise RuntimeError("unexpected fixed futures panel")
    return dates,fut,funding

def load_spot():
    spot={s:{} for s in S}
    base=CMROOT/"binance_spot"
    for s in S:
        for p in sorted((base/s).glob(f"{s}-1d-*.zip")):
            for r in parse_zip(p):
                if not r or not r[0].isdigit(): continue
                d=dfrom(r[0])
                if d<=END: spot[s][d]={"close":float(r[4])}
    return spot

def load_cm():
    import csv as _csv
    cm={a:{} for a in A}
    for a in A:
        p=CMROOT/"coinmetrics_public"/f"{a}.csv"
        if not p.exists(): raise RuntimeError(f"gateway 002 public Coin Metrics snapshot missing: {p}")
        with p.open(newline="",encoding="utf-8") as f:
            for row in _csv.DictReader(f):
                d=row.get("time","")[:10]
                if not d: continue
                vals={}
                for k,v in row.items():
                    if k=="time" or v in (None,""): continue
                    try: vals[k]=float(v)
                    except (TypeError,ValueError): pass
                if vals: cm[a][d]=vals
    return cm

def week_ends(dates):
    return [d for d in dates if datetime.fromisoformat(d).date().weekday()==6]

def score(cid,sd,dates,cm,spot,fut):
    day=datetime.fromisoformat(sd).date()
    ranked=[]
    for a,s in zip(A,S):
        prior=sorted(d for d in dates if d<sd)
        if not prior: return None
        if cid=="FIN-0079":
            end=prior[-1]; start=(datetime.fromisoformat(end).date()-timedelta(days=6))
            nd=sum(cm[a].get(d,{}).get("AdrNewCnt",0.0) for d in cm[a]
                   if start<=datetime.fromisoformat(d).date()<=datetime.fromisoformat(end).date())
            px=spot[s].get(end,{}).get("close")
            if nd<=0 or not px or px<=0: return None
            val=math.log(nd/px)
        elif cid=="FIN-0080":
            obs=[]
            for d in prior[-30:]:
                v=cm[a].get(d,{}).get("VolumeTotUSD"); m=cm[a].get(d,{}).get("CapMrktCurUSD")
                if v is not None and m is not None and m>0: obs.append(v/m)
            if len(obs)<25: return None
            val=statistics.stdev(obs)
        elif cid=="FIN-0082":
            end=prior[-1]
            sp=spot[s].get(end,{}).get("close"); fp=fut[s].get(end)
            if sp is None or fp is None or fp<=0: return None
            val=(sp-fp)/fp
        else: raise ValueError(cid)
        ranked.append((val,s))
    return ranked

def rank_weights(scores,cid):
    scores=sorted(scores,key=lambda x:(x[0],x[1]))
    w={s:0.0 for s in S}
    for _,s in scores[:3]:
        w[s]=1/6
    for _,s in scores[-3:]:
        w[s]=-1/6
    return w

def build_targets(cid,dates,cm,spot,fut):
    out={}; attempts=usable=0
    for d in dates:
        if datetime.fromisoformat(d).date().weekday()!=0: continue
        attempts+=1
        z=score(cid,d,dates,cm,spot,fut)
        if z is not None: out[d]=rank_weights(z,cid); usable+=1
    return out,attempts,usable

def simulate(dates,fut,funding,targets,mult):
    eq=1.0; prev={s:0.0 for s in S}; curve=[]; turnover=0.0
    for i,d in enumerate(dates):
        for s in S:
            for rate in funding[s].get(d,[]): eq*=1-prev[s]*rate
        if i:
            pd=dates[i-1]
            eq*=1+sum(prev[s]*(fut[s][d]/fut[s][pd]-1.0) for s in S)
        if d in targets:
            t=targets[d]; delta=sum(abs(t[s]-prev[s]) for s in S)
            eq*=max(0.0,1-(FEE+SLIP)*mult*delta); turnover+=delta/2; prev=t.copy()
        curve.append(eq)
    liq=sum(abs(v) for v in prev.values())
    eq*=max(0.0,1-(FEE+SLIP)*mult*liq); curve[-1]=eq
    return curve,turnover

def metrics(curve):
    rr=[curve[i]/curve[i-1]-1 for i in range(1,len(curve))]
    sd=statistics.stdev(rr) if len(rr)>1 else 0.0
    sh=statistics.mean(rr)/sd*math.sqrt(365.25) if sd else 0.0
    peak=curve[0]; mdd=0.0
    for x in curve: peak=max(peak,x); mdd=min(mdd,x/peak-1)
    return {"cumulative_return":curve[-1]-1,"sharpe":sh,"max_drawdown":mdd,"observations":len(curve)}

def prefix(curve,dates,end):
    idx=[i for i,d in enumerate(dates) if d<=end]
    base=curve[idx[0]-1] if idx and idx[0]>0 else 1.0
    return metrics([1.0]+[curve[i]/base for i in idx])

def oos_metrics(curve,dates):
    idx=[i for i,d in enumerate(dates) if d>=OOS_START]
    base=curve[idx[0]-1] if idx[0]>0 else 1.0
    return metrics([1.0]+[curve[i]/base for i in idx])

def benchmark(dates,fut):
    idx=[i for i,d in enumerate(dates) if d>=OOS_START]; a=idx[0]
    btc=[fut["BTCUSDT"][d]/fut["BTCUSDT"][dates[a]] for d in dates[a:]]
    ew=[1.0]
    for i in range(a+1,len(dates)):
        pd=dates[i-1]; ew.append(ew[-1]*(1+sum((fut[s][dates[i]]/fut[s][pd]-1)/8 for s in S)))
    return btc,ew

def residual_sharpe(sc,bc,ec):
    sr=[sc[i]/sc[i-1]-1 for i in range(1,len(sc))]
    br=[bc[i]/bc[i-1]-1 for i in range(1,len(bc))]
    er=[ec[i]/ec[i-1]-1 for i in range(1,len(ec))]
    n=min(len(sr),len(br),len(er)); sr=sr[:n]; br=br[:n]; er=er[:n]
    if n<10: return 0.0
    import numpy as np
    X=np.column_stack([np.ones(n),np.array(br),np.array(er)])
    y=np.array(sr)
    b=np.linalg.lstsq(X,y,rcond=None)[0]; res=y-X@b
    sd=float(np.std(res,ddof=1))
    return float(np.mean(res)/sd*math.sqrt(365.25)) if sd else 0.0

def main():
    dates,fut,funding=load_panel(); spot=load_spot(); cm=load_cm()
    results={}; passed=[]; blocked=[]
    for cid in ["FIN-0079","FIN-0080","FIN-0082"]:
        targets,attempts,usable=build_targets(cid,dates,cm,spot,fut)
        coverage=usable/attempts if attempts else 0
        if usable<40 or coverage<0.80:
            results[cid]={"status":"DATA_BLOCKED","attempts":attempts,"usable":usable,"coverage":coverage}; blocked.append(cid); continue
        runs={}
        for mult in (1.0,2.0):
            curve,turn=simulate(dates,fut,funding,targets,mult)
            runs[f"{mult:.1f}x"]={"discovery":prefix(curve,dates,DISCOVERY_END),"turnover":turn,"rebalances":sum(d<=DISCOVERY_END for d in targets)}
        ok=runs["1.0x"]["discovery"]["cumulative_return"]>0 and runs["1.0x"]["discovery"]["sharpe"]>0 and runs["1.0x"]["rebalances"]>=20
        results[cid]={"status":"CHEAP_PASS" if ok else "CHEAP_FAIL","attempts":attempts,"usable":usable,"coverage":coverage,"runs":runs}
        if ok: passed.append(cid)
    passed.sort(key=lambda c:(results[c]["runs"]["2.0x"]["discovery"]["sharpe"],c),reverse=True)
    selected=passed[:CAP]; btc,ew=benchmark(dates,fut); deep={}
    for cid in selected:
        targets,_,_=build_targets(cid,dates,cm,spot,fut)
        curves={}
        for mult in (1.0,2.0,3.0):
            curve,turn=simulate(dates,fut,funding,targets,mult)
            curves[f"{mult:.1f}x"]={"oos":oos_metrics(curve,dates),"turnover":turn}
        curve,_=simulate(dates,fut,funding,targets,1.0)
        oi=[i for i,d in enumerate(dates) if d>=OOS_START]; base=curve[oi[0]-1]
        sc=[curve[i]/base for i in oi]; bc=[x/btc[0] for x in btc]; ec=[x/ew[0] for x in ew]
        mid=len(oi)//2
        first=metrics([1.0]+[curve[i]/base for i in oi[:mid]])
        second_base=curve[oi[mid]-1]
        second=metrics([1.0]+[curve[i]/second_base for i in oi[mid:]])
        deep[cid]={"runs":curves,"oos_halves":{"first":first,"second":second},
                   "benchmark_oos":{"BTCUSDT_buy_and_hold":btc[-1]-1,"equal_weight_long_only":ew[-1]-1},
                   "residual_sharpe":residual_sharpe(sc,bc,ec)}
    result={"batch_id":"HARMONY-DISCOVERY-BATCH-015","results":results,"passed_cheap":passed,"selected_for_deep":selected,"deep":deep,
            "integrity":{"holdout_access":False,"parameter_search":False,"universe_search":False,"direction_search":False,"candidate_mutation":False}}
    OUT.mkdir(parents=True,exist_ok=True)
    raw=(json.dumps(result,sort_keys=True,indent=2)+"\n").encode()
    (OUT/"HARMONY-DISCOVERY-BATCH-015-RESULT.json").write_bytes(raw)
    (OUT/"SUMMARY.json").write_text(json.dumps({"batch_id":result["batch_id"],"passed_cheap":passed,"selected_for_deep":selected,
        "result_sha256":hashlib.sha256(raw).hexdigest()},sort_keys=True,indent=2)+"\n")
    print(json.dumps({"passed_cheap":passed,"selected_for_deep":selected,"result_sha256":hashlib.sha256(raw).hexdigest(),"blocked":blocked},indent=2))

if __name__=="__main__":
    main()
