from __future__ import annotations
import concurrent.futures,csv,hashlib,json,math,statistics,urllib.request,zipfile
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import coint,adfuller

ROOT=Path(__file__).resolve().parents[1]
RAW=ROOT/"data/cache/harmony_gateway_v8/raw_4h"
OUT=ROOT/"artifacts/HARMONY-STATARB-4H-026"
ASSETS=["APTUSDT","ARBUSDT","OPUSDT","INJUSDT","SUIUSDT","SEIUSDT","TIAUSDT","JTOUSDT","PYTHUSDT","WIFUSDT","ENAUSDT","ORDIUSDT","JUPUSDT"]
START="2023-01-01";END="2025-10-31";DISCOVERY_END="2024-05-21";OOS_START="2024-05-22"
BASE_URL="https://data.binance.vision/data/futures/um/monthly/klines"

def sha(b):return hashlib.sha256(b).hexdigest()

def months():
    out=[];y,m=2023,1
    while (y,m)<=(2025,10):
        out.append((y,m));m+=1
        if m==13:y,m=y+1,1
    return out

def ensure_raw(pair):
    d=RAW/pair
    if list(d.glob("*.zip")):return
    d.mkdir(parents=True,exist_ok=True)
    def get(ym):
        y,m=ym;fn=f"{pair}-4h-{y}-{m:02d}.zip";url=f"{BASE_URL}/{pair}/4h/{fn}"
        req=urllib.request.Request(url,headers={"User-Agent":"Harmony-StatArb-4H-026"})
        try:
            with urllib.request.urlopen(req,timeout=60) as r:raw=r.read()
            return ym,raw,None
        except Exception as e:return ym,None,str(e)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for ym,raw,err in ex.map(get,months()):
            if raw is not None:(d/f"{pair}-4h-{ym[0]}-{ym[1]:02d}.zip").write_bytes(raw)

def load_pair(pair):
    ensure_raw(pair);rows=[]
    for p in sorted((RAW/pair).glob("*.zip")):
        with zipfile.ZipFile(p) as z:
            ns=[n for n in z.namelist() if n.lower().endswith(".csv")]
            if len(ns)!=1:continue
            for r in csv.reader(z.read(ns[0]).decode("utf-8","replace").splitlines()):
                if r and r[0].isdigit() and len(r)>=6:
                    dt=datetime.fromtimestamp(int(r[0])/1000,timezone.utc)
                    d=dt.date().isoformat()
                    if START<=d<=END:rows.append((dt, float(r[1]),float(r[4])))
    dd={int(t.timestamp()): (t,o,c) for t,o,c in rows}
    rows=sorted(dd.values(),key=lambda x:x[0])
    out=pd.DataFrame(rows,columns=["timestamp","open","close"]).set_index("timestamp")
    out.attrs["pair"]=pair
    return out

def hedge(y,x):
    X=np.column_stack([np.ones(len(x)),x])
    coef=np.linalg.lstsq(X,y,rcond=None)[0]
    return float(coef[1]),float(coef[0])

def half_life(spread):
    s=np.asarray(spread,float)
    if len(s)<20:return None
    lag=s[:-1];delta=s[1:]-s[:-1]
    X=np.column_stack([np.ones(len(lag)),lag]);b=np.linalg.lstsq(X,delta,rcond=None)[0][1]
    return float(-math.log(2)/b) if b<0 else None

def pair_test(a,b):
    al=a["close"].loc[a.index<=DISCOVERY_END];bl=b["close"].loc[b.index<=DISCOVERY_END]
    df=pd.concat([np.log(al),np.log(bl)],axis=1,join="inner").dropna()
    if len(df)<800:return None
    y=df.iloc[:,0].values;x=df.iloc[:,1].values
    stat,pv,_=coint(y,x,trend="c");beta,inter=hedge(y,x);spread=y-(inter+beta*x);hl=half_life(spread)
    bounds=[];n=len(df);w=n//3
    for i in range(3):
        sub=df.iloc[i*w:(i+1)*w if i<2 else n]
        if len(sub)<250:continue
        st,p,_=coint(sub.iloc[:,0],sub.iloc[:,1],trend="c");bounds.append(float(p))
    adf_p=float(adfuller(spread,autolag="AIC")[1])
    stable=sum(p<.10 for p in bounds)>=2
    return {"a":a.attrs["pair"],"b":b.attrs["pair"],"bars":len(df),"coint_p":float(pv),"adf_resid_p":adf_p,"beta":beta,"intercept":inter,
            "half_life_bars":hl,"window_pvalues":bounds,"stable":stable}

def build_diagnostics(series):
    pairs=[]
    for i,a in enumerate(ASSETS):
        for b in ASSETS[i+1:]:
            r=pair_test(series[a],series[b])
            if r:pairs.append(r)
    valid=[r for r in pairs if r["coint_p"]<.05 and r["stable"] and r["half_life_bars"] is not None and 2<=r["half_life_bars"]<=240]
    valid=sorted(valid,key=lambda r:(r["coint_p"],abs(math.log(max(r["half_life_bars"],1))),r["a"],r["b"]))
    selected=valid[:5]
    return pairs,selected

def spread_series(df_a,df_b,beta,intercept,rolling=False,lookback=240):
    idx=df_a.index.intersection(df_b.index)
    a=np.log(df_a.loc[idx,"close"].values);b=np.log(df_b.loc[idx,"close"].values)
    if not rolling:
        s=a-(intercept+beta*b);return pd.Series(s,index=idx)
    out=[]
    for i in range(len(idx)):
        if i<lookback:out.append(np.nan);continue
        yy=a[i-lookback:i];xx=b[i-lookback:i];bb,ii=hedge(yy,xx);out.append(a[i]-(ii+bb*b[i]))
    return pd.Series(out,index=idx)

def trade_pair(df_a,df_b,beta,intercept,mode,entry,exit,max_hold,cost_bps):
    s=spread_series(df_a,df_b,beta,intercept,rolling=(mode=="rolling"))
    z=(s-s.rolling(120).mean())/(s.rolling(120).std()+1e-12)
    idx=s.index
    eq=1.;curve=[(idx[0],eq)] if len(idx) else [];pos=0;entry_px=None;entry_i=None;side_pair=None;trades=[]
    cost=cost_bps/10000
    for i in range(1,len(idx)-1):
        ts=idx[i]; nxt=idx[i+1]
        za=z.iloc[i]
        # Exit at next open if threshold or max hold reached.
        if pos!=0:
            should_exit=(pos==1 and za>=-exit) or (pos==-1 and za<=exit) or (i-entry_i>=max_hold)
            if should_exit:
                a0=float(df_a.loc[ts,"close"]);b0=float(df_b.loc[ts,"close"])
                a1=float(df_a.loc[nxt,"open"]);b1=float(df_b.loc[nxt,"open"])
                # Long spread: +A - beta*B, short spread opposite.
                gross=(math.log(a1/a0)-beta*math.log(b1/b0))*pos/(1+abs(beta))
                eq*=1+gross;eq*=max(0,1-cost*(1+abs(beta)));trades.append(gross);pos=0;entry_i=None
        if pos==0 and math.isfinite(za):
            if za<=-entry:pos=1
            elif za>=entry:pos=-1
            if pos!=0:
                # charge entry at next open.
                eq*=max(0,1-cost*(1+abs(beta)));entry_i=i
        curve.append((nxt,eq))
    return curve,trades

def basket_test(selected,series,kind,cost_bps):
    all_curves=[];trade_ct=0
    selected_for_kind = selected[:1] if kind=="SA05" else selected
    for p in selected_for_kind:
        a,b=p["a"],p["b"];aa=series[a];bb=series[b]
        if kind=="SA01":
            c,t=trade_pair(aa,bb,p["beta"],p["intercept"],"static",2,.5,48,cost_bps)
        elif kind=="SA02":
            c,t=trade_pair(aa,bb,p["beta"],p["intercept"],"static",1.5,.25,48,cost_bps)
        elif kind=="SA03":
            c,t=trade_pair(aa,bb,p["beta"],p["intercept"],"rolling",2,.5,48,cost_bps)
        elif kind=="SA04":
            c,t=trade_pair(aa,bb,p["beta"],p["intercept"],"rolling",2,.5,96,cost_bps)
        elif kind=="SA05":
            c,t=trade_pair(aa,bb,p["beta"],p["intercept"],"static",2,.5,48,cost_bps)
        else:
            raise KeyError(kind)
        all_curves.append(c);trade_ct+=len(t)
    if not all_curves:return [],0
    # Align by timestamp and equal-weight log equity returns across selected pairs.
    eq={ts:[] for ts,_ in all_curves[0]}
    for c in all_curves:
        for ts,v in c:eq.setdefault(ts,[]).append(v)
    timeline=sorted(eq);curve=[];prev=None;value=1.
    for ts in timeline:
        vals=eq[ts]
        if prev is not None:
            rets=[vals[i]/prev[i]-1 for i in range(min(len(vals),len(prev))) if prev[i]>0]
            if rets:value*=1+sum(rets)/len(rets)
        curve.append((ts,value));prev=vals
    return curve,trade_ct

def metrics(curve,start,end):
    xs=[v for ts,v in curve if start<=ts.date().isoformat()<=end]
    if len(xs)<3:return {"return":0,"sharpe":0,"mdd":0,"observations":len(xs)}
    rr=[xs[i]/xs[i-1]-1 for i in range(1,len(xs))];sd=statistics.stdev(rr) if len(rr)>1 else 0
    sh=statistics.mean(rr)/sd*math.sqrt(365.25*6) if sd else 0
    pk=xs[0];mdd=0
    for x in xs:pk=max(pk,x);mdd=min(mdd,x/pk-1)
    return {"return":xs[-1]/xs[0]-1,"sharpe":sh,"mdd":mdd,"observations":len(xs)}

def main():
    OUT.mkdir(parents=True,exist_ok=True);series={a:load_pair(a) for a in ASSETS}
    pairs,selected=build_diagnostics(series)
    (OUT/"COINTEGRATION-DIAGNOSTICS.json").write_text(json.dumps({"all_pairs":pairs,"selected_pairs":selected},sort_keys=True,indent=2)+"\n")
    names=["SA01","SA02","SA03","SA04","SA05"];results={};passed=[]
    for cid in names:
        costs={}
        for bps in (11,16.5,22):
            curve,trades=basket_test(selected,series,cid,bps)
            costs[str(bps)]={"discovery":metrics(curve,"2023-01-01",DISCOVERY_END),"oos":metrics(curve,OOS_START,END),"trades":trades}
        d11=costs["11"]["discovery"];d22=costs["22"]["discovery"]
        ok=costs["11"]["trades"]>=50 and d11["return"]>0 and d22["return"]>0 and d11["sharpe"]>=.50 and d22["sharpe"]>=.25
        results[cid]={"status":"CHEAP_PASS" if ok else "CHEAP_FAIL","costs":costs}
        if ok:passed.append(cid)
    passed.sort(key=lambda c:(results[c]["costs"]["22"]["discovery"]["sharpe"],c),reverse=True);selected_mech=passed[:3];deep={};promo=[]
    for cid in selected_mech:
        stress={}
        for bps in (11,16.5,22,27.5,33):
            c,t=basket_test(selected,series,cid,bps);stress[str(bps)]={"oos":metrics(c,OOS_START,END),"trades":t}
        c,_=basket_test(selected,series,cid,11);oos=[x for x in c if x[0].date().isoformat()>=OOS_START];mid=len(oos)//2
        first=metrics(oos[:mid],OOS_START,OOS_START);second=metrics(oos[mid:],OOS_START,END)
        deep[cid]={"stress":stress,"oos_halves":{"first":first,"second":second}}
        if stress["11"]["oos"]["sharpe"]>=.9 and stress["22"]["oos"]["sharpe"]>=.5 and first["sharpe"]>0 and second["sharpe"]>0:promo.append(cid)
    result={"batch_id":"HARMONY-STATARB-4H-026","selected_pairs":selected,"pair_count":len(pairs),"results":results,"passed_cheap":passed,"selected_for_deep":selected_mech,"promoted_guarded":promo,
            "integrity":{"parameter_search":False,"universe_search":False,"direction_search":False,"pair_mutation_after_oos":False,"holdout_access":False,"blue_chip_assets_excluded":True}}
    raw=json.dumps(result,sort_keys=True,indent=2).encode();(OUT/"HARMONY-STATARB-4H-026-RESULT.json").write_bytes(raw)
    (OUT/"SUMMARY.json").write_text(json.dumps({"batch_id":result["batch_id"],"pair_count":len(pairs),"selected_pairs":selected,"passed_cheap":passed,"selected_for_deep":selected_mech,"promoted_guarded":promo,"result_sha256":hashlib.sha256(raw).hexdigest()},sort_keys=True,indent=2)+"\n")
    print(json.dumps({"pair_count":len(pairs),"selected_pairs":selected,"passed_cheap":passed,"selected_for_deep":selected_mech,"promoted_guarded":promo},indent=2))
if __name__=="__main__":main()
