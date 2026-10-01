from __future__ import annotations
import csv, hashlib, json, math, statistics, zipfile
from datetime import datetime, timezone
from pathlib import Path

S=["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
DISCOVERY_END="2024-05-31"; OOS_START="2024-06-01"; END="2025-10-31"
FEE=.0006; SLIP=.0005; CAP=2
FROOT=Path("data/cache/binance/futures_um/deep_history_2019")
VROOT=Path("data/cache/harmony_gateway_v7/features")
OUT=Path("artifacts/HARMONY-DISCOVERY-BATCH-023")

def parse_zip(p):
    import zipfile
    with zipfile.ZipFile(p) as z:
        ns=[n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(ns)!=1: raise RuntimeError(f"unexpected archive {p}")
        return list(csv.reader(z.read(ns[0]).decode("utf-8","replace").splitlines()))

def dfrom(ms):
    x=int(ms); return datetime.fromtimestamp(x/(1_000_000 if x>=100_000_000_000_000 else 1000),timezone.utc).date().isoformat()

def load_monthly_crypto():
    daily={s:{} for s in S}
    for s in S:
        for p in sorted((FROOT/"klines"/s/"1d").glob(f"{s}-1d-*.zip")):
            for r in parse_zip(p):
                if r and r[0].isdigit():
                    d=dfrom(r[0])
                    if d<=END: daily[s][d]=float(r[4])
    dates=sorted(set.intersection(*(set(daily[s]) for s in S)))
    monthly={s:{} for s in S}
    months=sorted(set(d[:7] for d in dates))
    prev_close={}
    for m in months:
        ds=[d for d in dates if d[:7]==m]
        if not ds: continue
        for s in S:
            start_px=daily[s][ds[0]]; end_px=daily[s][ds[-1]]
            if start_px>0 and end_px>0: monthly[s][m]=math.log(end_px/start_px)
    return monthly

def load_external():
    p=VROOT/"external_uncertainty_monthly.csv"
    if not p.exists(): raise RuntimeError(f"missing {p}")
    finu={}; epu={}
    with p.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            m=r["month"]
            if r["finu_log_change"] not in ("",None) and r["epu_monthly_mean"] not in ("",None):
                finu[m]=float(r["finu_log_change"]); epu[m]=float(r["epu_monthly_mean"])
    # Use month-over-month log EPU change as the policy-uncertainty control.
    epu_change={}; prev=None
    for m in sorted(epu):
        if prev and prev>0 and epu[m]>0: epu_change[m]=math.log(epu[m]/prev)
        prev=epu[m]
    return finu,epu_change

def ols_beta(y,x):
    n=min(len(y),len(x))
    if n<18:return None
    y=y[-n:];x=x[-n:]; my=sum(y)/n;mx=sum(x)/n
    den=sum((v-mx)**2 for v in x)
    return sum((y[i]-my)*(x[i]-mx) for i in range(n))/den if den>0 else None

def ols_beta_resid(y,x,z):
    n=min(len(y),len(x),len(z))
    if n<18:return None
    x=x[-n:];z=z[-n:];y=y[-n:]
    mx=sum(x)/n;mz=sum(z)/n;my=sum(y)/n
    # residualize x on z.
    denz=sum((v-mz)**2 for v in z)
    if denz<=0:return None
    b=sum((x[i]-mx)*(z[i]-mz) for i in range(n))/denz
    xr=[x[i]-(mx+b*(z[i]-mz)) for i in range(n)]
    mxr=sum(xr)/n
    den=sum((v-mxr)**2 for v in xr)
    return sum((y[i]-my)*(xr[i]-mxr) for i in range(n))/den if den>0 else None

def beta_series(asset,months,ret,finu,window=24,positive_only=False,residual=False,epu=None):
    out={}
    ms=sorted(set(ret[asset]) & set(finu))
    for i,m in enumerate(ms):
        if i+1<window: continue
        sample=ms[i-window+1:i+1]
        y=[ret[asset][q] for q in sample];x=[finu[q] for q in sample]
        if positive_only:
            keep=[j for j,v in enumerate(x) if v>0]
            y=[y[j] for j in keep];x=[x[j] for j in keep]
            if len(y)<18:continue
            out[m]=ols_beta(y,x)
        elif residual:
            z=[epu.get(q) for q in sample]
            if any(v is None for v in z):continue
            out[m]=ols_beta_resid(y,x,z)
        else:
            out[m]=ols_beta(y,x)
    return out

def score(cid,m,ret,finu,epu):
    scores=[]
    for s in S:
        b=beta_series(s,sorted(ret[s]),ret,finu,24,
                       positive_only=(cid=="FIN-0117"),
                       residual=(cid=="FIN-0118"),epu=epu)
        if cid=="FIN-0115":
            x=b.get(m)
        elif cid=="FIN-0116":
            cur=b.get(m)
            idx=sorted(b); x=abs(cur-b[idx[-2]]) if cur is not None and len(idx)>=2 else None
        elif cid=="FIN-0117":
            x=b.get(m)
        elif cid=="FIN-0118":
            x=b.get(m)
        elif cid=="FIN-0119":
            cur=b.get(m); idx=sorted(ret[s]); prior=idx[-12:] if len(idx)>=12 else []
            vol=statistics.stdev([ret[s][q] for q in prior]) if len(prior)>1 else None
            x=(cur*vol) if cur is not None and vol is not None else None
        elif cid=="FIN-0120":
            bidx=sorted(b)
            if len(bidx)<6:x=None
            else:
                xs=list(range(6)); ys=[b[q] for q in bidx[-6:]]
                xm=sum(xs)/6;ym=sum(ys)/6;den=sum((v-xm)**2 for v in xs)
                x=sum((xs[i]-xm)*(ys[i]-ym) for i in range(6))/den if den else None
        else: raise KeyError(cid)
        if x is None or not math.isfinite(x): return None
        scores.append((x,s))
    return scores

def weights(z):
    z=sorted(z,key=lambda x:(x[0],x[1]));w={s:0 for s in S}
    for _,s in z[:3]:w[s]=-1/6
    for _,s in z[-3:]:w[s]=1/6
    return w

def metrics(c):
    rr=[c[i]/c[i-1]-1 for i in range(1,len(c))];sd=statistics.stdev(rr) if len(rr)>1 else 0
    sh=statistics.mean(rr)/sd*math.sqrt(12) if sd else 0
    pk=c[0];mdd=0
    for x in c:pk=max(pk,x);mdd=min(mdd,x/pk-1)
    return {"cumulative_return":c[-1]-1,"sharpe":sh,"max_drawdown":mdd,"observations":len(c)}

def seg(c,dates,a,b):
    ids=[i for i,d in enumerate(dates) if a<=d<=b]
    if not ids:return metrics([1])
    base=c[ids[0]-1] if ids[0]>0 else 1
    return metrics([1]+[c[i]/base for i in ids])

def run(cid,months,ret,finu,epu,mult):
    targets={}
    for m in months:
        z=score(cid,m,ret,finu,epu)
        if z is not None:targets[m]=weights(z)
    eq=1.;prev={s:0 for s in S};curve=[];turn=0;timeline=[]
    for i,m in enumerate(months):
        if i:
            pm=months[i-1]
            eq*=1+sum(prev[s]*(math.exp(ret[s][m])-1) for s in S)
        if m in targets:
            t=targets[m];delta=sum(abs(t[s]-prev[s]) for s in S);eq*=max(0,1-(FEE+SLIP)*mult*delta);turn+=delta/2;prev=t.copy()
        curve.append(eq);timeline.append(m)
    eq*=max(0,1-(FEE+SLIP)*mult*sum(abs(v) for v in prev.values()));curve[-1]=eq
    return curve,turn,len(targets)

def main():
    ret=load_monthly_crypto();finu,epu=load_external()
    months=sorted(set.intersection(*(set(ret[s]) for s in S)) & set(finu))
    N=["FIN-0115","FIN-0116","FIN-0117","FIN-0118","FIN-0119","FIN-0120"]
    res={};passed=[]
    for cid in N:
        c1,_,u=run(cid,months,ret,finu,epu,1);c2,_,_=run(cid,months,ret,finu,epu,2)
        d1=seg(c1,months,"2022-01","2024-05");d2=seg(c2,months,"2022-01","2024-05")
        ok=u>=18 and d1["cumulative_return"]>0 and d1["sharpe"]>0 and d2["cumulative_return"]>0 and d2["sharpe"]>0
        res[cid]={"status":"CHEAP_PASS" if ok else "CHEAP_FAIL","rebalances":u,"discovery_1x":d1,"discovery_2x":d2}
        if ok:passed.append(cid)
    passed.sort(key=lambda c:(res[c]["discovery_2x"]["sharpe"],c),reverse=True);selected=passed[:CAP];deep={}
    for cid in selected:
        curves={}
        for mult in (1,2,3,4,5):
            c,to,u=run(cid,months,ret,finu,epu,mult);curves[f"{mult}x"]={"oos":seg(c,months,OOS_START[:7],END[:7]),"turnover":to,"rebalances":u}
        c,_,_=run(cid,months,ret,finu,epu,1);ids=[i for i,m in enumerate(months) if m>=OOS_START[:7]];mid=len(ids)//2;base=c[ids[0]-1]
        first=metrics([1]+[c[i]/base for i in ids[:mid]]);sb=c[ids[mid]-1];second=metrics([1]+[c[i]/sb for i in ids[mid:]])
        deep[cid]={"runs":curves,"oos_halves":{"first":first,"second":second}}
    # Exploratory gateway: never mark guarded promotion even if the economic gate is met.
    promo=[]
    result={"batch_id":"HARMONY-DISCOVERY-BATCH-023","results":res,"passed_cheap":passed,
            "selected_for_deep":selected,"promoted_guarded":promo,"deep":deep,
            "integrity":{"holdout_access":False,"parameter_search":False,"universe_search":False,"direction_search":False,"candidate_mutation":False,
                         "vintage_proof":False,"guarded_promotion_blocked":True}}
    OUT.mkdir(parents=True,exist_ok=True);raw=(json.dumps(result,sort_keys=True,indent=2)+"\n").encode()
    (OUT/"HARMONY-DISCOVERY-BATCH-023-RESULT.json").write_bytes(raw)
    (OUT/"SUMMARY.json").write_text(json.dumps({"batch_id":result["batch_id"],"passed_cheap":passed,"selected_for_deep":selected,"promoted_guarded":promo,
                                                "result_sha256":hashlib.sha256(raw).hexdigest()},sort_keys=True,indent=2)+"\n")
    print(json.dumps({"passed_cheap":passed,"selected_for_deep":selected,"promoted_guarded":promo,
                      "result_sha256":hashlib.sha256(raw).hexdigest()},indent=2))
if __name__=="__main__":main()
