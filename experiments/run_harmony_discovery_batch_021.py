from __future__ import annotations
import csv, hashlib, json, math, statistics, zipfile
from datetime import datetime, timezone
from pathlib import Path

S=["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
DISCOVERY_END="2024-05-21"; OOS_START="2024-05-22"; END="2025-10-31"
FEE=.0006; SLIP=.0005; CAP=2
FROOT=Path("data/cache/binance/futures_um/deep_history_2019")
OUT=Path("artifacts/HARMONY-DISCOVERY-BATCH-021")

def parse_zip(p):
    with zipfile.ZipFile(p) as z:
        ns=[n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(ns)!=1: raise RuntimeError(f"unexpected archive {p}")
        return list(csv.reader(z.read(ns[0]).decode("utf-8","replace").splitlines()))

def dfrom(ms):
    x=int(ms); sec=x/1_000_000 if x>=100_000_000_000_000 else x/1000
    return datetime.fromtimestamp(sec,timezone.utc).date().isoformat()

def load_panel():
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

def arr(asset,date,ret):
    prior=sorted(k for k in ret[asset] if k<date)[-63:]
    return [ret[asset][k] for k in prior]

def downside_beta(asset,date,ret):
    r=ret[asset]; m=ret["BTCUSDT"]
    prior=sorted(set(r)&set(m))
    prior=[d for d in prior if d<date][-63:]
    neg=[d for d in prior if m[d]<0]
    if len(neg)<10:return None
    am=sum(r[d] for d in neg)/len(neg); mm=sum(m[d] for d in neg)/len(neg)
    den=sum((m[d]-mm)**2 for d in neg)
    return sum((r[d]-am)*(m[d]-mm) for d in neg)/den if den>0 else None

def quantile(xs,q):
    xs=sorted(xs)
    if not xs:return None
    k=(len(xs)-1)*q; lo=int(math.floor(k)); hi=int(math.ceil(k))
    return xs[lo] if lo==hi else xs[lo]+(xs[hi]-xs[lo])*(k-lo)

def score(cid,date,ret):
    z=[]
    for s in S:
        xs=arr(s,date,ret)
        if len(xs)<63:return None
        if cid=="FIN-0103":
            x=downside_beta(s,date,ret)
        elif cid=="FIN-0104":
            q=quantile(xs,.05); x=-q if q is not None else None
        elif cid=="FIN-0105":
            q=quantile(xs,.05); tail=[-r for r in xs if q is not None and r<=q]
            x=sum(tail)/len(tail) if tail else None
        elif cid=="FIN-0106":
            neg=[r for r in xs if r<0]; x=sum(r*r for r in neg)/len(neg) if neg else None
        elif cid=="FIN-0107":
            ql=quantile(xs,.05); qu=quantile(xs,.95)
            down=[-r for r in xs if ql is not None and r<=ql]
            up=[r for r in xs if qu is not None and r>=qu]
            x=(sum(down)/len(down)-sum(up)/len(up)) if down and up else None
        else: raise KeyError(cid)
        if x is None or not math.isfinite(x):return None
        z.append((x,s))
    return z

def weights(z):
    z=sorted(z,key=lambda x:(x[0],x[1])); w={s:0.0 for s in S}
    for _,s in z[:3]:w[s]=-1/6
    for _,s in z[-3:]:w[s]=1/6
    return w

def run(cid,dates,px,funding,ret,mult):
    targets={}; attempts=usable=0
    for d in dates:
        if datetime.fromisoformat(d).date().weekday()!=0:continue
        attempts+=1; z=score(cid,d,ret)
        if z is not None:targets[d]=weights(z); usable+=1
    eq=1.0; prev={s:0.0 for s in S}; curve=[]; turnover=0
    for i,d in enumerate(dates):
        for s in S:
            for rate in funding[s].get(d,[]):eq*=1-prev[s]*rate
        if i:
            pd=dates[i-1]
            eq*=1+sum(prev[s]*(px[s][d]/px[s][pd]-1) for s in S)
        if d in targets:
            t=targets[d]; delta=sum(abs(t[s]-prev[s]) for s in S)
            eq*=max(0,1-(FEE+SLIP)*mult*delta); turnover+=delta/2; prev=t.copy()
        curve.append(eq)
    liq=sum(abs(v) for v in prev.values()); eq*=max(0,1-(FEE+SLIP)*mult*liq); curve[-1]=eq
    return curve,targets,attempts,usable,turnover

def metrics(curve):
    rr=[curve[i]/curve[i-1]-1 for i in range(1,len(curve))]
    sd=statistics.stdev(rr) if len(rr)>1 else 0.0
    sh=statistics.mean(rr)/sd*math.sqrt(365.25) if sd else 0.0
    peak=curve[0];mdd=0.0
    for x in curve:peak=max(peak,x);mdd=min(mdd,x/peak-1)
    return {"cumulative_return":curve[-1]-1,"sharpe":sh,"max_drawdown":mdd,"observations":len(curve)}

def segment(curve,dates,start,end):
    ids=[i for i,d in enumerate(dates) if start<=d<=end]
    if not ids:return metrics([1.0])
    base=curve[ids[0]-1] if ids[0]>0 else 1.0
    return metrics([1.0]+[curve[i]/base for i in ids])

def main():
    dates,px,funding=load_panel(); ret={s:{d:math.log(px[s][d]/px[s][dates[i-1]]) for i,d in enumerate(dates) if i>0} for s in S}
    N=["FIN-0103","FIN-0104","FIN-0105","FIN-0106","FIN-0107"];results={};passed=[]
    for cid in N:
        c1,t1,a,u1,to=run(cid,dates,px,funding,ret,1.0);c2,t2,_,u2,_=run(cid,dates,px,funding,ret,2.0)
        d1=segment(c1,dates,"2020-07-10",DISCOVERY_END);d2=segment(c2,dates,"2020-07-10",DISCOVERY_END)
        ok=(u1>=20 and u1/a>=.8 and d1["cumulative_return"]>0 and d1["sharpe"]>0 and d2["cumulative_return"]>0 and d2["sharpe"]>0)
        results[cid]={"status":"CHEAP_PASS" if ok else "CHEAP_FAIL","attempts":a,"usable":u1,"coverage":u1/a if a else 0,"discovery_1x":d1,"discovery_2x":d2,"rebalances":u1}
        if ok:passed.append(cid)
    passed.sort(key=lambda c:(results[c]["discovery_2x"]["sharpe"],c),reverse=True);selected=passed[:CAP];deep={}
    for cid in selected:
        c1,t,_,_,to=run(cid,dates,px,funding,ret,1.0);curves={}
        for m in (1.,2.,3.,4.,5.):
            c,t,_,_,to=run(cid,dates,px,funding,ret,m);curves[f"{m:.1f}x"]={"oos":segment(c,dates,OOS_START,END),"turnover":to}
        ids=[i for i,d in enumerate(dates) if d>=OOS_START];mid=len(ids)//2;base=c1[ids[0]-1]
        first=metrics([1.0]+[c1[i]/base for i in ids[:mid]]);sb=c1[ids[mid]-1]
        second=metrics([1.0]+[c1[i]/sb for i in ids[mid:]])
        deep[cid]={"runs":curves,"oos_halves":{"first":first,"second":second}}
    promotions=[]
    for cid in selected:
        r=deep[cid]["runs"];h=deep[cid]["oos_halves"]
        if r["1.0x"]["oos"]["sharpe"]>=.9 and r["2.0x"]["oos"]["sharpe"]>.5 and r["3.0x"]["oos"]["sharpe"]>.5 and h["first"]["sharpe"]>0 and h["second"]["sharpe"]>0:promotions.append(cid)
    result={"batch_id":"HARMONY-DISCOVERY-BATCH-021","results":results,"passed_cheap":passed,"selected_for_deep":selected,"promoted_guarded":promotions,"deep":deep,"integrity":{"holdout_access":False,"parameter_search":False,"universe_search":False,"direction_search":False,"candidate_mutation":False}}
    OUT.mkdir(parents=True,exist_ok=True);raw=(json.dumps(result,sort_keys=True,indent=2)+"\n").encode()
    (OUT/"HARMONY-DISCOVERY-BATCH-021-RESULT.json").write_bytes(raw);(OUT/"SUMMARY.json").write_text(json.dumps({"batch_id":result["batch_id"],"passed_cheap":passed,"selected_for_deep":selected,"promoted_guarded":promotions,"result_sha256":hashlib.sha256(raw).hexdigest()},sort_keys=True,indent=2)+"\n")
    print(json.dumps({"passed_cheap":passed,"selected_for_deep":selected,"promoted_guarded":promotions,"result_sha256":hashlib.sha256(raw).hexdigest()},indent=2))
if __name__=="__main__":main()
