from __future__ import annotations
import csv,hashlib,json,math,statistics,zipfile
from datetime import datetime,timezone
from pathlib import Path

S=["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
DISCOVERY_END="2024-05-21";OOS_START="2024-05-22";END="2025-10-31";FEE=.0006;SLIP=.0005;CAP=2
FROOT=Path("data/cache/binance/futures_um/deep_history_2019");OUT=Path("artifacts/HARMONY-DISCOVERY-BATCH-024")

def zrows(p):
    with zipfile.ZipFile(p) as z:
        ns=[n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(ns)!=1:raise RuntimeError(f"unexpected archive {p}")
        return list(csv.reader(z.read(ns[0]).decode("utf-8","replace").splitlines()))

def dfrom(ms):
    x=int(ms);return datetime.fromtimestamp(x/(1_000_000 if x>=100_000_000_000_000 else 1000),timezone.utc).date().isoformat()

def load():
    panel={s:{} for s in S}
    for s in S:
        for p in sorted((FROOT/"klines"/s/"1d").glob(f"{s}-1d-*.zip")):
            for r in zrows(p):
                if not r or not r[0].isdigit() or len(r)<8:continue
                d=dfrom(r[0])
                if d<=END:
                    panel[s][d]={"close":float(r[4]),"quote":float(r[7])}
    dates=sorted(set.intersection(*(set(panel[s]) for s in S)))
    ret={s:{} for s in S};illiq={s:{} for s in S}
    for s in S:
        ds=sorted(panel[s])
        for i,d in enumerate(ds):
            if i==0:continue
            prev=ds[i-1];r=math.log(panel[s][d]["close"]/panel[s][prev]["close"])
            q=panel[s][d]["quote"];ret[s][d]=r
            illiq[s][d]=abs(r)/q if q>0 else None
    return dates,ret,illiq

def ols_beta(y,x):
    n=min(len(y),len(x))
    if n<40:return None
    y=y[-n:];x=x[-n:];my=sum(y)/n;mx=sum(x)/n;den=sum((v-mx)**2 for v in x)
    return sum((y[i]-my)*(x[i]-mx) for i in range(n))/den if den>0 else None

def rank_pct(vals):
    arr=sorted(vals.values())
    n=len(arr)
    return {k:(arr.index(v)/(n-1) if n>1 else .5) for k,v in vals.items()}

def snapshot(m,ret,illiq,cid):
    assets={}
    ms=sorted(illiq["BTCUSDT"])
    # Build market illiquidity from common cross-section at each date.
    market={d:statistics.mean([illiq[s][d] for s in S if illiq[s].get(d) is not None]) for d in ms if all(illiq[s].get(d) is not None for s in S)}
    for s in S:
        ds=sorted(set(illiq[s])&set(market))
        if m not in ds or len(ds)<70:return None
        idx=ds.index(m);w63=ds[max(0,idx-62):idx+1];w21=ds[max(0,idx-20):idx+1]
        y=[illiq[s][d] for d in w63];x=[market[d] for d in w63]
        if cid in ("FIN-0121","FIN-0122","FIN-0123","FIN-0124"):
            if cid=="FIN-0122":
                yd=[y[i]-y[i-1] for i in range(1,len(y))];xd=[x[i]-x[i-1] for i in range(1,len(x))]
                b=ols_beta(yd,xd);res=[yd[i]-(sum(yd)/len(yd)+(b or 0)*(xd[i]-sum(xd)/len(xd))) for i in range(len(yd))] if b is not None else None
            else:
                b=ols_beta(y,x);res=[y[i]-(sum(y)/len(y)+(b or 0)*(x[i]-sum(x)/len(x))) for i in range(len(y))] if b is not None else None
            if b is None:return None
            if cid=="FIN-0121":value=b
            elif cid=="FIN-0122":value=b
            elif cid=="FIN-0123":value=statistics.stdev(res) if res and len(res)>1 else None
            else:
                y21=[illiq[s][d] for d in w21];x21=[market[d] for d in w21]
                b21=ols_beta(y21,x21);value=abs(b-b21) if b21 is not None else None
        elif cid=="FIN-0125":
            prior=sorted(ret[s]);r126=[ret[s][d] for d in prior if d<m][-126:]
            mom=sum(r126)
            vals={a:statistics.mean([illiq[a][d] for d in w21]) for a in S if all(illiq[a].get(d) is not None for d in w21)}
            ranks=rank_pct(vals);value=mom*ranks[s]
        elif cid=="FIN-0126":
            vals_i={a:statistics.mean([illiq[a][d] for d in w21]) for a in S if all(illiq[a].get(d) is not None for d in w21)}
            vals_r={a:statistics.mean([ret[a][d]**2 for d in sorted(ret[a]) if d<m][-63:]) for a in S}
            ranks_i=rank_pct(vals_i);ranks_r=rank_pct(vals_r);value=ranks_i[s]*ranks_r[s]
        else:raise KeyError(cid)
        if value is None or not math.isfinite(value):return None
        assets[s]=value
    return assets

def weights(score):
    z=sorted(score.items(),key=lambda x:(x[1],x[0]));w={s:0 for s in S}
    for s,_ in z[:3]:w[s]=-1/6
    for s,_ in z[-3:]:w[s]=1/6
    return w

def metrics(c):
    rr=[c[i]/c[i-1]-1 for i in range(1,len(c))];sd=statistics.stdev(rr) if len(rr)>1 else 0
    sh=statistics.mean(rr)/sd*math.sqrt(365.25) if sd else 0
    pk=c[0];mdd=0
    for x in c:pk=max(pk,x);mdd=min(mdd,x/pk-1)
    return {"cumulative_return":c[-1]-1,"sharpe":sh,"max_drawdown":mdd,"observations":len(c)}

def segment(c,dates,a,b):
    ids=[i for i,d in enumerate(dates) if a<=d<=b]
    if not ids:return metrics([1])
    base=c[ids[0]-1] if ids[0]>0 else 1
    return metrics([1]+[c[i]/base for i in ids])

def run(cid,dates,ret,illiq,mult):
    targets={}
    for d in dates:
        if datetime.fromisoformat(d).date().weekday()!=0:continue
        z=snapshot(d,ret,illiq,cid)
        if z is not None:targets[d]=weights(z)
    eq=1.;prev={s:0 for s in S};curve=[];turn=0
    for i,d in enumerate(dates):
        if i:
            pd=dates[i-1];eq*=1+sum(prev[s]*(math.exp(ret[s][d])-1) for s in S)
        if d in targets:
            t=targets[d];delta=sum(abs(t[s]-prev[s]) for s in S);eq*=max(0,1-(FEE+SLIP)*mult*delta);turn+=delta/2;prev=t.copy()
        curve.append(eq)
    eq*=max(0,1-(FEE+SLIP)*mult*sum(abs(v) for v in prev.values()));curve[-1]=eq
    return curve,turn,len(targets)

def main():
    dates,ret,illiq=load();names=["FIN-0121","FIN-0122","FIN-0123","FIN-0124","FIN-0125","FIN-0126"];res={};passed=[]
    for cid in names:
        c1,_,u=run(cid,dates,ret,illiq,1);c2,_,_=run(cid,dates,ret,illiq,2);d1=segment(c1,dates,"2020-07-10",DISCOVERY_END);d2=segment(c2,dates,"2020-07-10",DISCOVERY_END)
        ok=u>=20 and d1["cumulative_return"]>0 and d1["sharpe"]>0 and d2["cumulative_return"]>0 and d2["sharpe"]>0
        res[cid]={"status":"CHEAP_PASS" if ok else "CHEAP_FAIL","rebalances":u,"discovery_1x":d1,"discovery_2x":d2}
        if ok:passed.append(cid)
    passed.sort(key=lambda c:(res[c]["discovery_2x"]["sharpe"],c),reverse=True);selected=passed[:CAP];deep={}
    for cid in selected:
        curves={}
        for mult in (1,2,3,4,5):
            c,to,u=run(cid,dates,ret,illiq,mult);curves[f"{mult}x"]={"oos":segment(c,dates,OOS_START,END),"turnover":to,"rebalances":u}
        c,_,_=run(cid,dates,ret,illiq,1);ids=[i for i,d in enumerate(dates) if d>=OOS_START];mid=len(ids)//2;base=c[ids[0]-1]
        first=metrics([1]+[c[i]/base for i in ids[:mid]]);sb=c[ids[mid]-1];second=metrics([1]+[c[i]/sb for i in ids[mid:]])
        deep[cid]={"runs":curves,"oos_halves":{"first":first,"second":second}}
    promo=[]
    for cid in selected:
        r=deep[cid]["runs"];h=deep[cid]["oos_halves"]
        if r["1x"]["oos"]["sharpe"]>=.90 and r["2x"]["oos"]["sharpe"]>.50 and r["3x"]["oos"]["sharpe"]>.50 and h["first"]["sharpe"]>0 and h["second"]["sharpe"]>0:promo.append(cid)
    result={"batch_id":"HARMONY-DISCOVERY-BATCH-024","results":res,"passed_cheap":passed,"selected_for_deep":selected,"promoted_guarded":promo,"deep":deep,
            "integrity":{"holdout_access":False,"parameter_search":False,"universe_search":False,"direction_search":False,"candidate_mutation":False}}
    OUT.mkdir(parents=True,exist_ok=True);raw=(json.dumps(result,sort_keys=True,indent=2)+"\n").encode()
    (OUT/"HARMONY-DISCOVERY-BATCH-024-RESULT.json").write_bytes(raw);(OUT/"SUMMARY.json").write_text(json.dumps({"batch_id":result["batch_id"],"passed_cheap":passed,"selected_for_deep":selected,"promoted_guarded":promo,"result_sha256":hashlib.sha256(raw).hexdigest()},sort_keys=True,indent=2)+"\n")
    print(json.dumps({"passed_cheap":passed,"selected_for_deep":selected,"promoted_guarded":promo,"result_sha256":hashlib.sha256(raw).hexdigest()},indent=2))
if __name__=="__main__":main()
