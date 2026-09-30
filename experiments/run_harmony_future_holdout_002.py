import csv,hashlib,io,json,math,statistics,zipfile
from datetime import date,datetime,timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,urlopen

SYMBOLS=["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
START=date(2026,9,1); MIN_OBS=180
ROOT=Path("data/cache/binance/futures_um/future_holdout_2026-09-forward/monthly")
OUT=Path("artifacts/HARMONY-FUTURE-HOLDOUT-002"); OUT.mkdir(parents=True,exist_ok=True)
FEE=.0006; SLIP=.0005; REBALANCE=7
CAND=["funding_carry_mean_1d_weekly_v1","funding_carry_mean_3d_weekly_v1","funding_carry_mean_7d_weekly_v1","funding_carry_zscore_7d_weekly_v1"]
FRONTIER="53adf24b8cdd4d055b9adfb4153ce6d26b8ee520074591943eecf373d4ea25c7"

def last_complete_month():
    now=datetime.now(timezone.utc).date()
    return date(now.year-1,12,1) if now.month==1 else date(now.year,now.month-1,1)

def month_pairs(start,end):
    out=[]; y,m=start.year,start.month
    while (y,m)<=(end.year,end.month):
        out.append((y,m)); m+=1
        if m==13:y,m=y+1,1
    return out

def fetch(url):
    req=Request(url,headers={"User-Agent":"Harmony/FUTURE-HOLDOUT-002"})
    with urlopen(req,timeout=120) as r:return r.read()

def sha(b):return hashlib.sha256(b).hexdigest()
def day(ms):return datetime.fromtimestamp(ms/1000,timezone.utc).date()

def ensure(symbol,kind,y,m):
    if kind=="price":
        name=f"{symbol}-1d-{y:04d}-{m:02d}.zip"; rel=Path("klines")/symbol/"1d"/name
        base=f"https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/1d/{name}"
    else:
        name=f"{symbol}-fundingRate-{y:04d}-{m:02d}.zip"; rel=Path("fundingRate")/symbol/name
        base=f"https://data.binance.vision/data/futures/um/monthly/fundingRate/{symbol}/{name}"
    path=ROOT/rel; path.parent.mkdir(parents=True,exist_ok=True)
    try: expected=fetch(base+".CHECKSUM").decode().strip().split()[0].lower()
    except HTTPError as exc:
        if exc.code==404: raise FileNotFoundError(base+".CHECKSUM")
        raise
    if path.exists():
        raw=path.read_bytes(); actual=sha(raw)
        if actual==expected:return {"path":str(path),"sha256":actual,"reused":True,"url":base}
        path.unlink()
    raw=fetch(base); actual=sha(raw)
    if actual!=expected: raise RuntimeError(f"CHECKSUM_MISMATCH:{base}")
    path.write_bytes(raw)
    return {"path":str(path),"sha256":actual,"reused":False,"url":base}

def zrows(path):
    with zipfile.ZipFile(path) as z:
        names=[n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(names)!=1:raise RuntimeError(f"ARCHIVE_SHAPE:{path}")
        return list(csv.reader(io.StringIO(z.read(names[0]).decode())))

latest=last_complete_month()
months=month_pairs(START,latest) if latest>=START else []
if not months:
    status={"protocol":"HARMONY-HOLDOUT-PROTOCOL-002","status":"WAITING_FOR_FIRST_COMPLETE_MONTH","observations":0,"minimum_required":MIN_OBS}
    (OUT/"gate-status.json").write_text(json.dumps(status,sort_keys=True,indent=2)+"\n"); print(json.dumps(status)); raise SystemExit(0)

price={s:{} for s in SYMBOLS}; funding={s:{} for s in SYMBOLS}; files=[]
for s in SYMBOLS:
    for y,m in months:
        try:
            pm=ensure(s,"price",y,m); fm=ensure(s,"funding",y,m)
        except FileNotFoundError as exc:
            status={"protocol":"HARMONY-HOLDOUT-PROTOCOL-002","status":"WAITING_FOR_COMPLETE_MONTH","missing_source":str(exc),"latest_complete_month":latest.isoformat(),"observations":0,"minimum_required":MIN_OBS}
            (OUT/"gate-status.json").write_text(json.dumps(status,sort_keys=True,indent=2)+"\n"); print(json.dumps(status)); raise SystemExit(0)
        files += [{"symbol":s,"year":y,"month":m,"kind":"price",**pm},{"symbol":s,"year":y,"month":m,"kind":"funding",**fm}]
        for r in zrows(ROOT/"klines"/s/"1d"/f"{s}-1d-{y:04d}-{m:02d}.zip"):
            if r and r[0].isdigit(): price[s][day(int(r[0]))]=float(r[4])
        rr=zrows(ROOT/"fundingRate"/s/f"{s}-fundingRate-{y:04d}-{m:02d}.zip")
        h={k.strip():i for i,k in enumerate(rr[0])}
        for r in rr[1:]:
            if r: funding[s].setdefault(day(int(r[h["calc_time"]])),[]).append(float(r[h["last_funding_rate"]]))

common=sorted(set.intersection(*(set(price[s]) for s in SYMBOLS))); common=[d for d in common if d>=START]
if len(common)<MIN_OBS:
    status={"protocol":"HARMONY-HOLDOUT-PROTOCOL-002","status":"WAITING_FOR_180_COMMON_OBSERVATIONS","latest_complete_month":latest.isoformat(),"common_start":common[0].isoformat() if common else None,"common_end":common[-1].isoformat() if common else None,"observations":len(common),"minimum_required":MIN_OBS}
    (OUT/"gate-status.json").write_text(json.dumps(status,sort_keys=True,indent=2)+"\n"); print(json.dumps(status)); raise SystemExit(0)

holdout=common[:MIN_OBS]; end=holdout[-1]
files.sort(key=lambda x:(x["symbol"],x["year"],x["month"],x["kind"]))
manifest={"protocol":"HARMONY-HOLDOUT-PROTOCOL-002","start":START.isoformat(),"end":end.isoformat(),"observations":MIN_OBS,"symbols":SYMBOLS,"files":files,"frontier_digest":FRONTIER}
mb=json.dumps(manifest,sort_keys=True,indent=2).encode()+b"\n"; msha=sha(mb); (OUT/"input-manifest.json").write_bytes(mb)

def mean_days(s,i,n): 
    seq=[r for dd in common[max(0,i-n):i] for r in funding[s].get(dd,[])]
    return statistics.mean(seq) if seq else None
def target(cid,i):
    vals=[]
    for s in SYMBOLS:
        m1=mean_days(s,i,1); m3=mean_days(s,i,3); m7=mean_days(s,i,7); seq=[r for dd in common[max(0,i-30):i] for r in funding[s].get(dd,[])]
        st=statistics.pstdev(seq) if len(seq)>1 else None
        if cid=="funding_carry_mean_1d_weekly_v1":score=-m1 if m1 is not None else None
        elif cid=="funding_carry_mean_3d_weekly_v1":score=-m3 if m3 is not None else None
        elif cid=="funding_carry_mean_7d_weekly_v1":score=-m7 if m7 is not None else None
        elif cid=="funding_carry_zscore_7d_weekly_v1":score=-(m7/st) if m7 is not None and st not in (None,0) else None
        else: raise ValueError(f"UNKNOWN_CANDIDATE:{cid}")
        if score is None:return None
        vals.append((score,s))
    vals.sort(key=lambda x:(-x[0],x[1])); w={s:0. for s in SYMBOLS}
    for _,s in vals[:2]:w[s]=.25
    for _,s in vals[-2:]:w[s]=-.25
    return w

def sim(cid,mult):
    eq=1.; prev={s:0. for s in SYMBOLS}; curve=[];turn=0.
    for j,dd in enumerate(holdout):
        for s in SYMBOLS:
            for rate in funding[s].get(dd,[]):eq*=1-prev[s]*rate
        if j:
            pd=holdout[j-1];eq*=1+sum(prev[s]*(price[s][dd]/price[s][pd]-1) for s in SYMBOLS)
        tgt=target(cid,common.index(dd)) if j%REBALANCE==0 else prev.copy()
        tgt=tgt or prev.copy()
        delta=sum(abs(tgt[s]-prev[s]) for s in SYMBOLS);turn+=delta/2;eq*=1-(FEE+SLIP)*mult*delta;prev=tgt;curve.append(eq)
    eq*=1-(FEE+SLIP)*mult*sum(abs(v) for v in prev.values());curve[-1]=eq
    rr=[curve[k]/curve[k-1]-1 for k in range(1,len(curve))];sd=statistics.stdev(rr) if len(rr)>1 else 0
    sh=(statistics.mean(rr)/sd)*math.sqrt(365.25) if sd else 0
    peak=curve[0];mdd=0
    for x in curve:peak=max(peak,x);mdd=min(mdd,x/peak-1)
    return {"cumulative_return":curve[-1]-1,"sharpe":sh,"max_drawdown":mdd,"one_way_turnover":turn}

def benchmark_equal_weight_long_only():
    curve=[1.0]
    for j in range(1,len(holdout)):
        pd,dd=holdout[j-1],holdout[j]
        ret=statistics.mean(price[s][dd]/price[s][pd]-1 for s in SYMBOLS)
        curve.append(curve[-1]*(1+ret))
    rr=[curve[k]/curve[k-1]-1 for k in range(1,len(curve))];sd=statistics.stdev(rr) if len(rr)>1 else 0
    sh=(statistics.mean(rr)/sd)*math.sqrt(365.25) if sd else 0
    peak=curve[0];mdd=0
    for x in curve:peak=max(peak,x);mdd=min(mdd,x/peak-1)
    return {"cumulative_return":curve[-1]-1,"sharpe":sh,"max_drawdown":mdd}

def benchmark_btc_buy_hold():
    curve=[1.0]
    for j in range(1,len(holdout)):
        pd,dd=holdout[j-1],holdout[j]; curve.append(curve[-1]*(price["BTCUSDT"][dd]/price["BTCUSDT"][pd]))
    rr=[curve[k]/curve[k-1]-1 for k in range(1,len(curve))];sd=statistics.stdev(rr) if len(rr)>1 else 0
    sh=(statistics.mean(rr)/sd)*math.sqrt(365.25) if sd else 0
    peak=curve[0];mdd=0
    for x in curve:peak=max(peak,x);mdd=min(mdd,x/peak-1)
    return {"cumulative_return":curve[-1]-1,"sharpe":sh,"max_drawdown":mdd}

benchmarks={"same_universe_equal_weight_long_only":benchmark_equal_weight_long_only(),"BTCUSDT_buy_and_hold":benchmark_btc_buy_hold()}
summary={"protocol":"HARMONY-HOLDOUT-PROTOCOL-002","status":"EXECUTED","start":START.isoformat(),"end":end.isoformat(),"observations":MIN_OBS,"input_manifest_sha256":msha,"benchmarks":benchmarks,"candidates":{}}
for cid in CAND:
    r={"candidate_id":cid,"input_manifest_sha256":msha,"frontier_digest":FRONTIER,"benchmarks":benchmarks,"base":sim(cid,1.0),"cost_stress":{f"{m:.1f}x":sim(cid,m) for m in (1,1.5,2)},"holdout_released_to_selection":False}
    raw=json.dumps(r,sort_keys=True,indent=2).encode()+b"\n"; rsha=sha(raw); (OUT/f"{cid}.json").write_bytes(raw)
    summary["candidates"][cid]={"result_sha256":rsha,"base":r["base"],"cost_stress":r["cost_stress"]}
(OUT/"summary.json").write_text(json.dumps(summary,sort_keys=True,indent=2)+"\n"); print(json.dumps(summary,sort_keys=True,indent=2))
