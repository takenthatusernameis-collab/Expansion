from __future__ import annotations
import csv,hashlib,json,math,statistics,zipfile
from datetime import datetime,timezone
from pathlib import Path
S=["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
DISCOVERY_END="2024-05-21";OOS_START="2024-05-22";END="2025-10-31";FEE=.0006;SLIP=.0005;CAP=2
FROOT=Path("data/cache/binance/futures_um/deep_history_2019");VROOT=Path("data/cache/harmony_gateway_v6/flow_4h");OUT=Path("artifacts/HARMONY-DISCOVERY-BATCH-022")
def parse_zip(p):
    with zipfile.ZipFile(p) as z:
        n=[x for x in z.namelist() if x.lower().endswith(".csv")]
        if len(n)!=1:raise RuntimeError(f"unexpected archive {p}")
        return list(csv.reader(z.read(n[0]).decode("utf-8","replace").splitlines()))
def dfrom(ms):
    x=int(ms);return datetime.fromtimestamp(x/(1_000_000 if x>=100_000_000_000_000 else 1000),timezone.utc).date().isoformat()
def load():
    px={s:{} for s in S};fund={s:{} for s in S}
    for s in S:
        for p in sorted((FROOT/"klines"/s/"1d").glob(f"{s}-1d-*.zip")):
            for r in parse_zip(p):
                if r and r[0].isdigit():
                    d=dfrom(r[0])
                    if d<=END:px[s][d]=float(r[4])
        for r in json.loads((FROOT/"funding_gateway"/f"{s}-2019-2025-10.json").read_text()):
            d=dfrom(r["fundingTime"])
            if d<=END:fund[s].setdefault(d,[]).append(float(r["fundingRate"]))
    dates=sorted(set.intersection(*(set(px[s]) for s in S)))
    if dates[0]>"2020-07-10" or dates[-1]!=END:raise RuntimeError("unexpected common panel")
    return dates,px,fund
def features():
    out={}
    for s in S:
        p=VROOT/f"{s}_flow_4h.csv"
        if not p.exists():raise RuntimeError(f"missing {p}")
        with p.open(encoding="utf-8") as fh:out[s]={r["date"]:r for r in csv.DictReader(fh)}
    return out
def score(cid,d,f):
    z=[]
    for s in S:
        r=f[s].get(d)
        if r is None:return None
        key={"FIN-0109":"flow_imbalance_21d","FIN-0110":"flow_z_21d","FIN-0111":"flow_return_product","FIN-0112":"flow_return_product","FIN-0113":"flow_abs_vol_interaction","FIN-0114":"flow_acceleration"}[cid]
        x=float(r[key])
        if cid=="FIN-0112":x=-x
        if not math.isfinite(x):return None
        z.append((x,s))
    return z
def weights(z):
    z=sorted(z,key=lambda x:(x[0],x[1]));w={s:0 for s in S}
    for _,s in z[:3]:w[s]=-1/6
    for _,s in z[-3:]:w[s]=1/6
    return w
def metrics(c):
    rr=[c[i]/c[i-1]-1 for i in range(1,len(c))];sd=statistics.stdev(rr) if len(rr)>1 else 0
    sh=statistics.mean(rr)/sd*math.sqrt(365.25) if sd else 0
    pk=c[0];mdd=0
    for x in c:pk=max(pk,x);mdd=min(mdd,x/pk-1)
    return {"cumulative_return":c[-1]-1,"sharpe":sh,"max_drawdown":mdd,"observations":len(c)}
def seg(c,dates,a,b):
    ids=[i for i,d in enumerate(dates) if a<=d<=b]
    if not ids:return metrics([1])
    base=c[ids[0]-1] if ids[0]>0 else 1
    return metrics([1]+[c[i]/base for i in ids])
def run(cid,dates,px,fund,f,mult):
    targets={}
    for d in dates:
        if datetime.fromisoformat(d).date().weekday()!=0:continue
        z=score(cid,d,f)
        if z is not None:targets[d]=weights(z)
    eq=1.;prev={s:0 for s in S};curve=[];turn=0
    for i,d in enumerate(dates):
        for s in S:
            for fr in fund[s].get(d,[]):eq*=1-prev[s]*fr
        if i:
            pd=dates[i-1];eq*=1+sum(prev[s]*(px[s][d]/px[s][pd]-1) for s in S)
        if d in targets:
            t=targets[d];delta=sum(abs(t[s]-prev[s]) for s in S);eq*=max(0,1-(FEE+SLIP)*mult*delta);turn+=delta/2;prev=t.copy()
        curve.append(eq)
    eq*=max(0,1-(FEE+SLIP)*mult*sum(abs(v) for v in prev.values()));curve[-1]=eq
    return curve,turn,len(targets)
def main():
    dates,px,fund=load();f=features();N=["FIN-0109","FIN-0110","FIN-0111","FIN-0112","FIN-0113","FIN-0114"];res={};passed=[]
    for cid in N:
        c1,_,u=run(cid,dates,px,fund,f,1);c2,_,_=run(cid,dates,px,fund,f,2)
        d1=seg(c1,dates,"2020-07-10",DISCOVERY_END);d2=seg(c2,dates,"2020-07-10",DISCOVERY_END)
        ok=u>=20 and d1["cumulative_return"]>0 and d1["sharpe"]>0 and d2["cumulative_return"]>0 and d2["sharpe"]>0
        res[cid]={"status":"CHEAP_PASS" if ok else "CHEAP_FAIL","rebalances":u,"discovery_1x":d1,"discovery_2x":d2}
        if ok:passed.append(cid)
    passed.sort(key=lambda c:(res[c]["discovery_2x"]["sharpe"],c),reverse=True);selected=passed[:CAP];deep={}
    for cid in selected:
        curves={}
        for m in (1,2,3,4,5):
            c,to,u=run(cid,dates,px,fund,f,m);curves[f"{m}x"]={"oos":seg(c,dates,OOS_START,END),"turnover":to,"rebalances":u}
        c,_,_=run(cid,dates,px,fund,f,1);ids=[i for i,d in enumerate(dates) if d>=OOS_START];mid=len(ids)//2;base=c[ids[0]-1]
        first=metrics([1]+[c[i]/base for i in ids[:mid]]);sb=c[ids[mid]-1];second=metrics([1]+[c[i]/sb for i in ids[mid:]])
        deep[cid]={"runs":curves,"oos_halves":{"first":first,"second":second}}
    promo=[]
    for cid in selected:
        r=deep[cid]["runs"];h=deep[cid]["oos_halves"]
        if r["1x"]["oos"]["sharpe"]>=.9 and r["2x"]["oos"]["sharpe"]>.5 and r["3x"]["oos"]["sharpe"]>.5 and h["first"]["sharpe"]>0 and h["second"]["sharpe"]>0:promo.append(cid)
    result={"batch_id":"HARMONY-DISCOVERY-BATCH-022","results":res,"passed_cheap":passed,"selected_for_deep":selected,"promoted_guarded":promo,"deep":deep,"integrity":{"holdout_access":False,"parameter_search":False,"universe_search":False,"direction_search":False,"candidate_mutation":False}}
    OUT.mkdir(parents=True,exist_ok=True);raw=(json.dumps(result,sort_keys=True,indent=2)+"\n").encode()
    (OUT/"HARMONY-DISCOVERY-BATCH-022-RESULT.json").write_bytes(raw);(OUT/"SUMMARY.json").write_text(json.dumps({"batch_id":result["batch_id"],"passed_cheap":passed,"selected_for_deep":selected,"promoted_guarded":promo,"result_sha256":hashlib.sha256(raw).hexdigest()},sort_keys=True,indent=2)+"\n")
    print(json.dumps({"passed_cheap":passed,"selected_for_deep":selected,"promoted_guarded":promo,"result_sha256":hashlib.sha256(raw).hexdigest()},indent=2))
if __name__=="__main__":main()
