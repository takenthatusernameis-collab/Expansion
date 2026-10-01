from __future__ import annotations
import csv, hashlib, json, math, statistics, zipfile
from datetime import datetime, timezone
from pathlib import Path

S=["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
DISCOVERY_END="2024-05-21"; OOS_START="2024-05-22"; END="2025-10-31"
FEE=.0006; SLIP=.0005; CAP=2
FROOT=Path("data/cache/binance/futures_um/deep_history_2019")
VROOT=Path("data/cache/harmony_gateway_v5/realized_2h")
OUT=Path("artifacts/HARMONY-DISCOVERY-BATCH-020")

def parse_zip(p):
    with zipfile.ZipFile(p) as z:
        ns=[n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(ns)!=1: raise RuntimeError(f"unexpected archive {p}")
        return list(csv.reader(z.read(ns[0]).decode("utf-8","replace").splitlines()))

def dfrom(ms):
    x=int(ms); sec=x/1_000_000 if x>=100_000_000_000_000 else x/1000
    return datetime.fromtimestamp(sec,timezone.utc).date().isoformat()

def load_daily():
    px={s:{} for s in S}; funding={s:{} for s in S}
    for s in S:
        for p in sorted((FROOT/"klines"/s/"1d").glob(f"{s}-1d-*.zip")):
            for r in parse_zip(p):
                if r and r[0].isdigit():
                    d=dfrom(r[0])
                    if d<=END: px[s][d]=float(r[4])
        fp=FROOT/"funding_gateway"/f"{s}-2019-2025-10.json"
        for r in json.loads(fp.read_text()):
            d=dfrom(r["fundingTime"])
            if d<=END: funding[s].setdefault(d,[]).append(float(r["fundingRate"]))
    dates=sorted(set.intersection(*(set(px[s]) for s in S)))
    if dates[0]>"2020-07-10" or dates[-1]!=END:
        raise RuntimeError(f"unexpected common panel {dates[0]}..{dates[-1]}")
    return dates,px,funding

def load_features():
    f={}
    for s in S:
        p=VROOT/f"{s}_realized_2h.csv"
        if not p.exists(): raise RuntimeError(f"missing gateway feature file {p}")
        with p.open(encoding="utf-8") as fh:
            f[s]={r["date"]:r for r in csv.DictReader(fh)}
    return f

def fv(r,k):
    v=r.get(k)
    return None if v in (None,"") else float(v)

def rolling_mean(vals):
    vals=[x for x in vals if x is not None and math.isfinite(x)]
    return sum(vals)/len(vals) if vals else None

def beta(asset,btc,window):
    pairs=[(asset[d],btc[d]) for d in sorted(set(asset)&set(btc))[-window:]]
    if len(pairs)<20: return None
    am=sum(x for x,_ in pairs)/len(pairs); bm=sum(y for _,y in pairs)/len(pairs)
    den=sum((y-bm)**2 for _,y in pairs)
    return sum((x-am)*(y-bm) for x,y in pairs)/den if den>0 else None

def score(cid,d,features,px):
    out=[]
    for s in S:
        r0=features[s].get(d)
        if r0 is None: return None
        if cid=="FIN-0097":
            x=fv(r0,"rv63")
        elif cid=="FIN-0098":
            prior=sorted(k for k in features[s] if k<d)[-21:]
            x=rolling_mean([fv(features[s][k],"rsj") for k in prior])
        elif cid=="FIN-0099":
            prior=sorted(k for k in features[s] if k<d)[-21:]
            x=rolling_mean([fv(features[s][k],"jump_share") for k in prior])
        elif cid=="FIN-0100":
            x=fv(r0,"vol_of_vol21")
        elif cid=="FIN-0101":
            a=fv(r0,"amihud21"); rv=fv(r0,"rv21"); x=(a*rv if a is not None and rv is not None else None)
        elif cid=="FIN-0102":
            prior=sorted(k for k in features[s] if k<d)[-21:]
            idv=[]
            btc=features["BTCUSDT"]
            for k in prior:
                rs=fv(features[s].get(k,{}),"ret_1d"); rb=fv(btc.get(k,{}),"ret_1d")
                rv=fv(features[s].get(k,{}),"rv"); brv=fv(btc.get(k,{}),"rv")
                if rs is None or rb is None or rv is None or brv is None: continue
                # beta uses daily returns from feature store, current and prior values only.
            asset_ret={k:fv(v,"ret_1d") for k,v in features[s].items() if k<d}
            btc_ret={k:fv(v,"ret_1d") for k,v in features["BTCUSDT"].items() if k<d}
            b=beta(asset_ret,btc_ret,63)
            vals=[]
            for k in prior:
                rv=fv(features[s].get(k,{}),"rv")
                brv=fv(features["BTCUSDT"].get(k,{}),"rv")
                if b is not None and rv is not None and brv is not None:
                    vals.append(max(0.0,rv*rv-b*b*brv*brv))
            x=rolling_mean(vals)
        else: raise KeyError(cid)
        if x is None or not math.isfinite(x): return None
        out.append((x,s))
    return out

def weights(z):
    z=sorted(z,key=lambda x:(x[0],x[1]))
    w={s:0.0 for s in S}
    for _,s in z[:3]: w[s]=-1/6
    for _,s in z[-3:]: w[s]=1/6
    return w

def schedule(d,cid):
    if cid=="FIN-0097":
        # Last Monday of each calendar month.
        return d.weekday()==0 and (d[5:7]!=next_day(d)[5:7] if False else True)
    return d.weekday()==0

def monthly_mondays(dates):
    out=[]; by={}
    for d in dates:
        if d.weekday()==0: by.setdefault(d[:7],[]).append(d)
    for v in by.values(): out.append(v[-1])
    return set(out)

def simulate(cid,dates,px,funding,features,mult):
    targets={}
    formation=monthly_mondays(dates) if cid=="FIN-0097" else {d for d in dates if datetime.fromisoformat(d).date().weekday()==0}
    attempts=usable=0
    for d in sorted(formation):
        if d<="2020-07-10" or d>END: continue
        attempts+=1
        z=score(cid,d,features,px)
        if z is not None:
            targets[d]=weights(z); usable+=1
    eq=1.0; prev={s:0.0 for s in S}; curve=[]; turnover=0.0
    for i,d in enumerate(dates):
        for s in S:
            for rate in funding[s].get(d,[]): eq*=1-prev[s]*rate
        if i:
            pd=dates[i-1]
            eq*=1+sum(prev[s]*(px[s][d]/px[s][pd]-1.0) for s in S)
        if d in targets:
            t=targets[d]
            delta=sum(abs(t[s]-prev[s]) for s in S)
            eq*=max(0.0,1-(FEE+SLIP)*mult*delta)
            turnover+=delta/2; prev=t.copy()
        curve.append(eq)
    liq=sum(abs(v) for v in prev.values())
    eq*=max(0.0,1-(FEE+SLIP)*mult*liq); curve[-1]=eq
    return curve,targets,attempts,usable,turnover

def metrics(curve):
    rr=[curve[i]/curve[i-1]-1 for i in range(1,len(curve))]
    sd=statistics.stdev(rr) if len(rr)>1 else 0.0
    sh=statistics.mean(rr)/sd*math.sqrt(365.25) if sd else 0.0
    peak=curve[0]; mdd=0.0
    for x in curve: peak=max(peak,x); mdd=min(mdd,x/peak-1)
    return {"cumulative_return":curve[-1]-1,"sharpe":sh,"max_drawdown":mdd,"observations":len(curve)}

def segment(curve,dates,start,end):
    ids=[i for i,d in enumerate(dates) if start<=d<=end]
    if not ids: return metrics([1.0])
    base=curve[ids[0]-1] if ids[0]>0 else 1.0
    return metrics([1.0]+[curve[i]/base for i in ids])

def residual_sharpe(sc,btc,ew):
    sr=[sc[i]/sc[i-1]-1 for i in range(1,len(sc))]
    br=[btc[i]/btc[i-1]-1 for i in range(1,len(btc))]
    er=[ew[i]/ew[i-1]-1 for i in range(1,len(ew))]
    n=min(len(sr),len(br),len(er))
    if n<20:return 0.0
    x1=statistics.mean(br[:n]); x2=statistics.mean(er[:n])
    y=statistics.mean(sr[:n])
    den1=sum((v-x1)**2 for v in br[:n]); den2=sum((v-x2)**2 for v in er[:n])
    # two-factor ordinary least squares using centered sums
    s11=den1; s22=den2; s12=sum((br[i]-x1)*(er[i]-x2) for i in range(n))
    sy1=sum((sr[i]-y)*(br[i]-x1) for i in range(n)); sy2=sum((sr[i]-y)*(er[i]-x2) for i in range(n))
    det=s11*s22-s12*s12
    if det<=1e-12:return 0.0
    b1=(sy1*s22-sy2*s12)/det; b2=(sy2*s11-sy1*s12)/det
    resid=[sr[i]-(y+b1*(br[i]-x1)+b2*(er[i]-x2)) for i in range(n)]
    sd=statistics.stdev(resid) if len(resid)>1 else 0
    return statistics.mean(resid)/sd*math.sqrt(365.25) if sd else 0.0

def main():
    dates,px,funding=load_daily(); features=load_features()
    NAMES=["FIN-0097","FIN-0098","FIN-0099","FIN-0100","FIN-0101","FIN-0102"]
    results={}; passed=[]
    for cid in NAMES:
        c1,t1,a,u1,to=simulate(cid,dates,px,funding,features,1.0)
        c2,t2,_,u2,_=simulate(cid,dates,px,funding,features,2.0)
        if u1<20 or a<20 or u1/a<0.80:
            results[cid]={"status":"DATA_BLOCKED","attempts":a,"usable":u1,"coverage":u1/a if a else 0.0}; continue
        d1=segment(c1,dates,"2020-07-10",DISCOVERY_END)
        d2=segment(c2,dates,"2020-07-10",DISCOVERY_END)
        ok=(d1["cumulative_return"]>0 and d1["sharpe"]>0 and d2["cumulative_return"]>0 and d2["sharpe"]>0 and u1>=20)
        results[cid]={"status":"CHEAP_PASS" if ok else "CHEAP_FAIL","attempts":a,"usable":u1,"coverage":u1/a,
                      "discovery_1x":d1,"discovery_2x":d2,"rebalances":u1}
        if ok: passed.append(cid)
    passed.sort(key=lambda c:(results[c]["discovery_2x"]["sharpe"],c),reverse=True)
    selected=passed[:CAP]; deep={}
    for cid in selected:
        curves={}
        target_oos={}
        for mult in (1.,2.,3.,4.,5.):
            c,t,a,u,to=simulate(cid,dates,px,funding,features,mult)
            curves[f"{mult:.1f}x"]={"oos":segment(c,dates,OOS_START,END),"turnover":to,"rebalances":len(t)}
            if mult==1.0: curve=c
        oos_ids=[i for i,d in enumerate(dates) if d>=OOS_START]
        base=curve[oos_ids[0]-1]
        sc=[curve[i]/base for i in oos_ids]
        btc=[px["BTCUSDT"][d]/px["BTCUSDT"][dates[oos_ids[0]]] for d in dates[oos_ids]]
        ew=[1.0]
        for j in range(1,len(oos_ids)):
            i=oos_ids[j]; prevd=dates[i-1]
            ew.append(ew[-1]*(1+sum((px[s][dates[i]]/px[s][prevd]-1)/8 for s in S)))
        mid=len(oos_ids)//2
        first=metrics([1.0]+[curve[i]/base for i in oos_ids[:mid]])
        second_base=curve[oos_ids[mid]-1]
        second=metrics([1.0]+[curve[i]/second_base for i in oos_ids[mid:]])
        deep[cid]={"runs":curves,"oos_halves":{"first":first,"second":second},
                   "benchmark_oos":{"btc":metrics([1.0]+[x/btc[0] for x in btc]),
                                    "equal_weight":metrics(ew)},
                   "residual_sharpe":residual_sharpe(sc,btc,ew)}
    promotions=[]
    for cid in selected:
        d=deep[cid]["runs"]
        h=deep[cid]["oos_halves"]
        if (d["1.0x"]["oos"]["sharpe"]>=0.9 and d["2.0x"]["oos"]["sharpe"]>0.5 and
            d["3.0x"]["oos"]["sharpe"]>0.5 and h["first"]["sharpe"]>0 and h["second"]["sharpe"]>0):
            promotions.append(cid)
    result={"batch_id":"HARMONY-DISCOVERY-BATCH-020","results":results,"passed_cheap":passed,
            "selected_for_deep":selected,"promoted_guarded":promotions,"deep":deep,
            "integrity":{"holdout_access":False,"parameter_search":False,"universe_search":False,
                         "direction_search":False,"candidate_mutation":False},
            "source_gateway":"HARMONY-DATA-GATEWAY-005","oos_start":OOS_START}
    OUT.mkdir(parents=True,exist_ok=True)
    raw=(json.dumps(result,sort_keys=True,indent=2)+"\n").encode()
    (OUT/"HARMONY-DISCOVERY-BATCH-020-RESULT.json").write_bytes(raw)
    (OUT/"SUMMARY.json").write_text(json.dumps({"batch_id":result["batch_id"],"passed_cheap":passed,
        "selected_for_deep":selected,"promoted_guarded":promotions,"result_sha256":hashlib.sha256(raw).hexdigest()},
        sort_keys=True,indent=2)+"\n")
    print(json.dumps({"passed_cheap":passed,"selected_for_deep":selected,"promoted_guarded":promotions,
                      "result_sha256":hashlib.sha256(raw).hexdigest()},indent=2))

if __name__=="__main__":
    main()
