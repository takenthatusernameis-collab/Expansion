import csv,io,json,math,statistics,zipfile
from pathlib import Path
from datetime import datetime,timezone,timedelta

S=["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
A=["btc","eth","ltc","xrp","bnb","bch","ada","doge"]
PAGE=dict(zip(A,["Bitcoin","Ethereum","Litecoin","XRP","BNB","Bitcoin_Cash","Cardano","Dogecoin"]))
ASSET_PATH=Path("data/cache/harmony_gateway_001")
ROOT=Path("data/cache/binance/futures_um/deep_history_2019")
OUT=Path("artifacts/HARMONY-DISCOVERY-BATCH-014")
DISCOVERY_END="2024-05-21"; OOS_START="2024-05-22"; END="2025-10-31"; FEE=.0006; SLIP=.0005; CAP=2

def parse_zip(p):
    with zipfile.ZipFile(p) as z:
        ns=[n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(ns)!=1: raise RuntimeError(f"unexpected archive: {p}")
        return list(csv.reader(io.StringIO(z.read(ns[0]).decode("utf-8"))))

def dfrom(ms): return datetime.fromtimestamp(int(ms)/1000,timezone.utc).date().isoformat()

def load_daily():
    px={s:{} for s in S}; funding={s:{} for s in S}
    for s in S:
        for p in sorted((ROOT/"klines"/s/"1d").glob(f"{s}-1d-*.zip")):
            for r in parse_zip(p):
                if r and r[0].isdigit():
                    d=dfrom(r[0])
                    if d<=END: px[s][d]=float(r[4])
        fp=ROOT/"funding_gateway"/f"{s}-2019-2025-10.json"
        for r in json.loads(fp.read_text()):
            d=dfrom(r["fundingTime"])
            if d<=END: funding[s].setdefault(d,[]).append(float(r["fundingRate"]))
    dates=sorted(set.intersection(*(set(px[s]) for s in S)))
    if (dates[0],dates[-1],len(dates))!=("2020-07-10",END,1935):
        raise RuntimeError(f"unexpected price panel {dates[:1]}..{dates[-1:]} n={len(dates)}")
    return dates,px,funding

def load_cm():
    p=ASSET_PATH/"coinmetrics_asset_metrics.json"
    if not p.exists(): raise RuntimeError("external Coin Metrics cache missing")
    rows=json.loads(p.read_text())
    return {a:{} for a in A},rows

def parse_cm(rows):
    out={a:{} for a in A}
    for r in rows:
        a=r.get("asset"); t=r.get("time","")[:10]
        if a in out and t:
            vals={}
            for k,v in r.items():
                if k not in ("asset","time") and v not in (None,""):
                    try: vals[k]=float(v)
                    except (TypeError,ValueError): pass
            if vals: out[a][t]=vals
    return out

def parse_wiki():
    out={a:{} for a in A}
    base=ASSET_PATH/"wikimedia"
    for a,page in PAGE.items():
        for p in sorted(base.glob(f"{page}-*.json")):
            payload=json.loads(p.read_text())
            for x in payload.get("items",[]):
                ts=str(x.get("timestamp","")); d=ts[:8]
                if len(d)==8:
                    iso=f"{d[:4]}-{d[4:6]}-{d[6:8]}"
                    out[a][iso]=float(x.get("views",0))
    return out

def parse_orderflow():
    out={s:{} for s in S}; base=ASSET_PATH/"binance_1h"
    missing=0
    for s in S:
        for p in sorted((base/s).glob(f"{s}-1h-*.zip")):
            for r in parse_zip(p):
                if not r or not r[0].isdigit(): continue
                day=dfrom(r[0])
                try:
                    q=float(r[7]); tb=float(r[10])
                except (TypeError,ValueError,IndexError):
                    continue
                z=out[s].setdefault(day,[0.0,0.0]); z[0]+=q; z[1]+=tb
    return out

def monday(d):
    return datetime.fromisoformat(d).date().weekday()==0

def prev_days(dates,i,n):
    return dates[max(0,i-n):i]

def week_sums(series,signal_day):
    dt=datetime.fromisoformat(signal_day).date()
    monday0=dt-timedelta(days=7)
    start=monday0-timedelta(days=6)
    vals=[]
    for d,v in series.items():
        dd=datetime.fromisoformat(d).date()
        if start<=dd<=monday0: vals.append(v)
    return vals

def score_for(cid,signal_day,cm,wiki,flow):
    sd=datetime.fromisoformat(signal_day).date()
    scores=[]
    for a,s in zip(A,S):
        if cid=="FIN-0071":
            days=sorted(d for d in cm[a] if datetime.fromisoformat(d).date()<sd)[-1:]
            if not days: return None
            r=cm[a][days[0]]
            x=r.get("AdrActCnt"); y=r.get("CapMrktCurUSD")
            if x is None or y is None or y<=0: return None
            val=x/y
        elif cid=="FIN-0072":
            days=sorted(d for d in cm[a] if datetime.fromisoformat(d).date()<sd)[-1:]
            if not days: return None
            val=cm[a][days[0]].get("CapMVRVCur")
            if val is None: return None
        elif cid=="FIN-0073":
            days=sorted(d for d in cm[a] if datetime.fromisoformat(d).date()<sd)
            if len(days)<31: return None
            d0=days[-31]; d1=days[-1]; v0=cm[a][d0].get("AdrActCnt"); v1=cm[a][d1].get("AdrActCnt")
            if v0 is None or v0<=0 or v1 is None: return None
            val=v1/v0-1.0
        elif cid=="FIN-0074":
            dt=sd-timedelta(days=1)
            w0=dt-timedelta(days=6)
            qsum=0.0; tbsum=0.0; nd=0
            for d,(q,tb) in flow[s].items():
                dd=datetime.fromisoformat(d).date()
                if w0<=dd<=dt and q>0:
                    qsum+=q; tbsum+=tb; nd+=1
            if nd<5 or qsum<=0: return None
            val=tbsum/qsum-0.5
        elif cid=="FIN-0075":
            dt=sd-timedelta(days=1); start7=dt-timedelta(days=6); start30=dt-timedelta(days=29)
            cur=[v for d,v in wiki[a].items() if start7<=datetime.fromisoformat(d).date()<=dt]
            hist=[v for d,v in wiki[a].items() if start30<=datetime.fromisoformat(d).date()<=dt]
            if len(cur)<5 or len(hist)<20: return None
            val=math.log((sum(cur)/len(cur)+1.0)/(sum(hist)/len(hist)+1.0))
        else: raise ValueError(cid)
        scores.append((val,s))
    return scores

def rank_weights(scores):
    scores=sorted(scores,key=lambda x:(x[0],x[1]))
    w={s:0.0 for s in S}
    for _,s in scores[:3]: w[s]=1/6
    for _,s in scores[-3:]: w[s]=-1/6
    return w

def build_targets(cid,dates,cm,wiki,flow):
    out={}; attempts=0; successes=0
    for i,d in enumerate(dates):
        if not monday(d): continue
        attempts+=1
        z=score_for(cid,d,cm,wiki,flow)
        if z is not None:
            out[d]=rank_weights(z); successes+=1
    return out,attempts,successes

def simulate(dates,px,funding,targets,mult=1.0):
    eq=1.0; prev={s:0.0 for s in S}; curve=[]; turnover=0.0
    for i,d in enumerate(dates):
        for s in S:
            for r in funding[s].get(d,[]): eq*=1-prev[s]*r
        if i>0:
            pd=dates[i-1]
            eq*=1.0+sum(prev[s]*(px[s][d]/px[s][pd]-1.0) for s in S)
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
    for x in curve: peak=max(peak,x); mdd=min(mdd,x/peak-1)
    return {"final_equity":curve[-1],"cumulative_return":curve[-1]-1.0,"sharpe":sh,"max_drawdown":mdd,"observations":len(curve)}

def segment(curve,dates,start):
    idx=[i for i,d in enumerate(dates) if d>=start]
    base=curve[idx[0]-1] if idx[0]>0 else 1.0
    return metrics([1.0]+[curve[i]/base for i in idx])

def halves(curve,dates,start):
    idx=[i for i,d in enumerate(dates) if d>=start]; m=len(idx)//2
    return segment(curve,dates,start), metrics([1.0]+[curve[i]/(curve[idx[0]+m-1] if idx[0]+m-1>=0 else 1.0) for i in idx[m:]])

def benchmark_curves(dates,px,funding):
    idx=[i for i,d in enumerate(dates) if d>=OOS_START]; a=idx[0]
    btc=[px["BTCUSDT"][d]/px["BTCUSDT"][dates[a]] for d in dates[a:]]
    ew=[1.0]
    for i in range(a+1,len(dates)):
        pd=dates[i-1]; ew.append(ew[-1]*(1+sum((1/8)*(px[s][dates[i]]/px[s][pd]-1) for s in S)))
    return btc,ew

def residual_sharpe(strategy_curve,btc_curve,ew_curve):
    sr=[strategy_curve[i]/strategy_curve[i-1]-1 for i in range(1,len(strategy_curve))]
    br=[btc_curve[i]/btc_curve[i-1]-1 for i in range(1,len(btc_curve))]
    er=[ew_curve[i]/ew_curve[i-1]-1 for i in range(1,len(ew_curve))]
    if len(sr)<3: return 0.0
    import statistics as st
    xb=sum(br)/len(br); xe=sum(er)/len(er); xs=sum(sr)/len(sr)
    vb=sum((x-xb)**2 for x in br); ve=sum((x-xe)**2 for x in er)
    if vb==0 or ve==0: return 0.0
    covb=sum((x-xb)*(y-xs) for x,y in zip(br,sr)); cove=sum((x-xe)*(y-xs) for x,y in zip(er,sr))
    den=vb*ve-covb*cove
    if den==0: return 0.0
    beta_b=(covb*ve-cove*sum((x-xb)*(y-xe) for x,y in zip(br,er)))/den
    beta_e=(cove*vb-covb*sum((x-xb)*(y-xe) for x,y in zip(br,er)))/den
    res=[y-beta_b*b-beta_e*e for y,b,e in zip(sr,br,er)]
    sd=st.stdev(res) if len(res)>1 else 0.0
    return (st.mean(res)/sd*math.sqrt(365.25)) if sd else 0.0

def main():
    dates,px,funding=load_daily()
    _,rawcm=load_cm(); cm=parse_cm(rawcm); wiki=parse_wiki(); flow=parse_orderflow()
    results={}; passed=[]; data_blocked=[]
    for cid in ["FIN-0071","FIN-0072","FIN-0073","FIN-0074","FIN-0075"]:
        targets,attempts,successes=build_targets(cid,dates,cm,wiki,flow)
        coverage=successes/attempts if attempts else 0
        if successes<40 or coverage<0.80:
            results[cid]={"status":"DATA_BLOCKED","attempted_signal_dates":attempts,"usable_signal_dates":successes,"coverage":coverage}; data_blocked.append(cid); continue
        runs={}
        discovery_targets={d:w for d,w in targets.items() if d<=DISCOVERY_END}
        for mult in (1.0,2.0):
            curve,turn=simulate(dates,px,funding,targets,mult)
            disc=segment(curve,dates,"1900-01-01")
            # Strict discovery prefix: only observations through the frozen discovery boundary may affect screening.
            di=[i for i,d in enumerate(dates) if d<=DISCOVERY_END]
            if not di:
                raise RuntimeError("empty discovery prefix")
            base=curve[di[0]-1] if di[0]>0 else 1.0
            disc=metrics([1.0]+[curve[i]/base for i in di])
            disc.update({"start":dates[di[0]],"end":dates[di[-1]],"observations":len(di)})
            runs[f"{mult:.1f}x"]={"discovery":disc,"turnover":turn,"rebalances":len(discovery_targets)}
        ok=runs["1.0x"]["discovery"]["cumulative_return"]>0 and runs["1.0x"]["discovery"]["sharpe"]>0 and runs["1.0x"]["rebalances"]>=20
        results[cid]={"status":"CHEAP_PASS" if ok else "CHEAP_FAIL","coverage":coverage,"usable_signal_dates":successes,"runs":runs}
        if ok: passed.append(cid)
    passed.sort(key=lambda c:(results[c]["runs"]["2.0x"]["discovery"]["sharpe"],c),reverse=True)
    selected=passed[:CAP]; deep={}
    btc,ew=benchmark_curves(dates,px,funding)
    for cid in selected:
        targets,_,_=build_targets(cid,dates,cm,wiki,flow); curves={}
        for mult in (1.0,2.0,3.0):
            curve,turn=simulate(dates,px,funding,targets,mult); oos=segment(curve,dates,OOS_START)
            curves[f"{mult:.1f}x"]={"oos":oos,"turnover":turn}
        basecurve,_=simulate(dates,px,funding,targets,1.0)
        a=[i for i,d in enumerate(dates) if d>=OOS_START][0]; sc=[basecurve[i] for i in range(a,len(dates))]
        sc=[x/sc[0] for x in sc]; bbtc=[x/btc[0] for x in btc]; bew=[x/ew[0] for x in ew]
        h=[i for i,d in enumerate(dates) if d>=OOS_START]; mid=len(h)//2
        first=metrics([1.0]+[basecurve[i]/(basecurve[h[0]-1] if h[0]>0 else 1.0) for i in h[:mid]])
        second=metrics([1.0]+[basecurve[i]/basecurve[h[mid]-1] for i in h[mid:]])
        deep[cid]={"runs":curves,"oos_halves":{"first":first,"second":second},
                   "benchmark_oos":{"btc_cumulative_return":bbtc[-1]-1,"equal_weight_cumulative_return":bew[-1]-1},
                   "residual_sharpe_vs_btc_and_equal_weight":residual_sharpe(sc,bbtc,bew)}
    result={"batch_id":"HARMONY-DISCOVERY-BATCH-014","results":results,"passed_cheap":passed,"selected_for_deep":selected,"deep":deep,
      "integrity":{"holdout_access":False,"parameter_search":False,"universe_search":False,"direction_search":False,"candidate_mutation":False}}
    OUT.mkdir(parents=True,exist_ok=True)
    raw=(json.dumps(result,sort_keys=True,indent=2)+"\n").encode(); (OUT/"HARMONY-DISCOVERY-BATCH-014-RESULT.json").write_bytes(raw)
    print(json.dumps({"passed_cheap":passed,"selected_for_deep":selected,"result_sha256":__import__("hashlib").sha256(raw).hexdigest(),"data_blocked":data_blocked},indent=2))

if __name__=="__main__": main()
