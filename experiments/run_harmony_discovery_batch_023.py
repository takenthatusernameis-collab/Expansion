from __future__ import annotations
import csv, hashlib, json, math, statistics, zipfile
from datetime import datetime, timezone
from pathlib import Path

S=["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
DISCOVERY_END="2024-05";OOS_START="2024-06";END="2025-10";FEE=.0006;SLIP=.0005;CAP=2
FROOT=Path("data/cache/binance/futures_um/deep_history_2019")
VROOT=Path("data/cache/harmony_gateway_v7/features")
OUT=Path("artifacts/HARMONY-DISCOVERY-BATCH-023")

def rows_zip(p):
    with zipfile.ZipFile(p) as z:
        ns=[n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(ns)!=1: raise RuntimeError(f"unexpected archive {p}")
        return list(csv.reader(z.read(ns[0]).decode("utf-8","replace").splitlines()))

def dfrom(ms):
    x=int(ms);return datetime.fromtimestamp(x/(1_000_000 if x>=100_000_000_000_000 else 1000),timezone.utc).date().isoformat()

def load_monthly_crypto():
    daily={s:{} for s in S}
    for s in S:
        for p in sorted((FROOT/"klines"/s/"1d").glob(f"{s}-1d-*.zip")):
            for r in rows_zip(p):
                if r and r[0].isdigit():
                    d=dfrom(r[0])
                    if d<="2025-10-31":daily[s][d]=float(r[4])
    months=sorted(set.intersection(*(set(d[:7] for d in daily[s]) for s in S)))
    ret={s:{} for s in S}
    for m in months:
        ds=[d for d in sorted(set.intersection(*(set(daily[s]) for s in S))) if d[:7]==m]
        if not ds: continue
        for s in S:
            a=daily[s][ds[0]];b=daily[s][ds[-1]]
            if a>0 and b>0:ret[s][m]=math.log(b/a)
    return ret

def load_finu():
    p=VROOT/"external_uncertainty_monthly.csv"
    if not p.exists():raise RuntimeError(f"missing {p}")
    out={}
    with p.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["finu_log_change"] not in ("",None):out[r["month"]]=float(r["finu_log_change"])
    return out

def beta(y,x):
    n=min(len(y),len(x))
    if n<18:return None
    y=y[-n:];x=x[-n:];my=sum(y)/n;mx=sum(x)/n;den=sum((v-mx)**2 for v in x)
    return sum((y[i]-my)*(x[i]-mx) for i in range(n))/den if den>0 else None

def beta_history(asset,ret,finu,positive_only=False):
    out={};ms=sorted(set(ret[asset])&set(finu))
    for i,m in enumerate(ms):
        if i+1<24:continue
        sample=ms[i-23:i+1];x=[finu[q] for q in sample];y=[ret[asset][q] for q in sample]
        if positive_only:
            k=[j for j,v in enumerate(x) if v>0];x=[x[j] for j in k];y=[y[j] for j in k]
        b=beta(y,x)
        if b is not None:out[m]=b
    return out

def score(cid,m,ret,finu):
    vals=[]
    for s in S:
        b=beta_history(s,ret,finu,positive_only=(cid=="FIN-0117"))
        if m not in b:return None
        if cid=="FIN-0115":x=b[m]
        elif cid=="FIN-0116":
            k=sorted(b);x=abs(b[m]-b[k[-2]]) if len(k)>=2 else None
        elif cid=="FIN-0117":x=b[m]
        elif cid=="FIN-0118":
            idx=sorted(ret[s]);vols=idx[-12:] if len(idx)>=12 else []
            vol=statistics.stdev([ret[s][q] for q in vols]) if len(vols)>1 else None
            x=b[m]*vol if vol is not None else None
        elif cid=="FIN-0119":
            k=sorted(b)
            if len(k)<6:x=None
            else:
                xs=list(range(6));ys=[b[q] for q in k[-6:]];xm=sum(xs)/6;ym=sum(ys)/6;den=sum((v-xm)**2 for v in xs)
                x=sum((xs[i]-xm)*(ys[i]-ym) for i in range(6))/den if den else None
        else:raise KeyError(cid)
        if x is None or not math.isfinite(x):return None
        vals.append((x,s))
    return vals

def weights(z):
    z=sorted(z,key=lambda q:(q[0],q[1]));w={s:0 for s in S}
    for _,s in z[:3]:w[s]=-1/6
    for _,s in z[-3:]:w[s]=1/6
    return w

def metrics(c):
    rr=[c[i]/c[i-1]-1 for i in range(1,len(c))];sd=statistics.stdev(rr) if len(rr)>1 else 0
    sh=statistics.mean(rr)/sd*math.sqrt(12) if sd else 0
    pk=c[0];mdd=0
    for x in c:pk=max(pk,x);mdd=min(mdd,x/pk-1)
    return {"cumulative_return":c[-1]-1,"sharpe":sh,"max_drawdown":mdd,"observations":len(c)}

def segment(c,dates,start,end):
    ids=[i for i,d in enumerate(dates) if start<=d<=end]
    if not ids:return metrics([1])
    base=c[ids[0]-1] if ids[0]>0 else 1
    return metrics([1]+[c[i]/base for i in ids])

def run(cid,dates,ret,finu,mult):
    targets={}
    for m in dates:
        z=score(cid,m,ret,finu)
        if z is not None:targets[m]=weights(z)
    eq=1.;prev={s:0 for s in S};curve=[];turn=0
    for i,m in enumerate(dates):
        if i:
            pm=dates[i-1];eq*=1+sum(prev[s]*(math.exp(ret[s][m])-1) for s in S)
        if m in targets:
            t=targets[m];delta=sum(abs(t[s]-prev[s]) for s in S);eq*=max(0,1-(FEE+SLIP)*mult*delta);turn+=delta/2;prev=t.copy()
        curve.append(eq)
    eq*=max(0,1-(FEE+SLIP)*mult*sum(abs(v) for v in prev.values()));curve[-1]=eq
    return curve,turn,len(targets)

def main():
    ret=load_monthly_crypto();finu=load_finu();dates=sorted(set.intersection(*(set(ret[s]) for s in S))&set(finu))
    names=["FIN-0115","FIN-0116","FIN-0117","FIN-0118","FIN-0119"];res={};passed=[]
    for cid in names:
        c1,_,u=run(cid,dates,ret,finu,1);c2,_,_=run(cid,dates,ret,finu,2);d1=segment(c1,dates,"2022-01",DISCOVERY_END);d2=segment(c2,dates,"2022-01",DISCOVERY_END)
        ok=u>=18 and d1["cumulative_return"]>0 and d1["sharpe"]>0 and d2["cumulative_return"]>0 and d2["sharpe"]>0
        res[cid]={"status":"CHEAP_PASS" if ok else "CHEAP_FAIL","rebalances":u,"discovery_1x":d1,"discovery_2x":d2}
        if ok:passed.append(cid)
    passed.sort(key=lambda c:(res[c]["discovery_2x"]["sharpe"],c),reverse=True);selected=passed[:CAP];deep={}
    for cid in selected:
        curves={}
        for mult in (1,2,3,4,5):
            c,to,u=run(cid,dates,ret,finu,mult);curves[f"{mult}x"]={"oos":segment(c,dates,OOS_START,END),"turnover":to,"rebalances":u}
        c,_,_=run(cid,dates,ret,finu,1);ids=[i for i,d in enumerate(dates) if d>=OOS_START];mid=len(ids)//2;base=c[ids[0]-1]
        first=metrics([1]+[c[i]/base for i in ids[:mid]]);sb=c[ids[mid]-1];second=metrics([1]+[c[i]/sb for i in ids[mid:]])
        deep[cid]={"runs":curves,"oos_halves":{"first":first,"second":second}}
    result={"batch_id":"HARMONY-DISCOVERY-BATCH-023","results":res,"passed_cheap":passed,"selected_for_deep":selected,"promoted_guarded":[],"deep":deep,
            "integrity":{"holdout_access":False,"parameter_search":False,"universe_search":False,"direction_search":False,"candidate_mutation":False,
                         "vintage_proof":False,"guarded_promotion_blocked":True}}
    OUT.mkdir(parents=True,exist_ok=True);raw=(json.dumps(result,sort_keys=True,indent=2)+"\n").encode()
    (OUT/"HARMONY-DISCOVERY-BATCH-023-RESULT.json").write_bytes(raw)
    (OUT/"SUMMARY.json").write_text(json.dumps({"batch_id":result["batch_id"],"passed_cheap":passed,"selected_for_deep":selected,"promoted_guarded":[],"result_sha256":hashlib.sha256(raw).hexdigest()},sort_keys=True,indent=2)+"\n")
    print(json.dumps({"passed_cheap":passed,"selected_for_deep":selected,"promoted_guarded":[],"result_sha256":hashlib.sha256(raw).hexdigest()},indent=2))
if __name__=="__main__":main()
