import csv,hashlib,io,json,math,statistics,zipfile
from datetime import date,datetime,timezone,timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request,urlopen

SYMBOLS=["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
START=date(2026,9,1); MIN_OBS=180
ROOT=Path("data/cache/binance/futures_um/elapsed_history_2026-09-daily-v5")
OUT=Path("artifacts/HARMONY-FUTURE-HOLDOUT-003"); OUT.mkdir(parents=True,exist_ok=True)
FEE=.0006; SLIP=.0005; REBALANCE=7
CAND=["funding_carry_mean_1d_weekly_v1","funding_carry_mean_3d_weekly_v1","funding_carry_mean_7d_weekly_v1","funding_carry_zscore_7d_weekly_v1"]
CANDIDATE_SCOPE_DIGEST=hashlib.sha256(json.dumps(CAND,separators=(",",":"),ensure_ascii=True).encode()).hexdigest()
FRONTIER="53adf24b8cdd4d055b9adfb4153ce6d26b8ee520074591943eecf373d4ea25c7"

def fetch(url):
    req=Request(url,headers={"User-Agent":"Harmony/FUTURE-HOLDOUT-003"})
    with urlopen(req,timeout=120) as r:return r.read()
def sha(b):return hashlib.sha256(b).hexdigest()
def expected_checksum(url):
    try: raw=fetch(url+".CHECKSUM").decode("utf-8","replace").strip().split()
    except HTTPError as exc:
        if exc.code==404:return None
        raise
    if not raw:raise ValueError("EMPTY_CHECKSUM:"+url)
    token=raw[0].lower()
    if len(token)!=64:raise ValueError("INVALID_CHECKSUM:"+url)
    int(token,16);return token
def days(a,b):
    d=a
    while d<=b:
        yield d
        d+=timedelta(days=1)
def daily_price(symbol,dd):
    name=f"{symbol}-1d-{dd:%Y-%m-%d}.zip"
    base=f"https://data.binance.vision/data/futures/um/daily/klines/{symbol}/1d/{name}"
    p=ROOT/"klines"/symbol/"1d"/name;p.parent.mkdir(parents=True,exist_ok=True)
    expected=expected_checksum(base)
    if p.exists():
        raw=p.read_bytes();actual=sha(raw)
        if raw and (expected is None or actual==expected):
            return {"key":f"price|{symbol}|{dd}","source_url":base,"path":str(p),"size_bytes":len(raw),"sha256":actual,"expected_sha256":expected,"reused":True}
        p.unlink()
    raw=fetch(base);actual=sha(raw)
    if not raw:raise ValueError("EMPTY_PRICE:"+base)
    if expected is not None and actual!=expected:raise ValueError("PRICE_CHECKSUM_MISMATCH:"+base)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if z.testzip() is not None:raise ValueError("PRICE_ZIP_CRC:"+base)
        names=[n for n in z.namelist() if n.endswith(".csv")]
        if len(names)!=1:raise ValueError("PRICE_ARCHIVE_SHAPE:"+base)
    p.write_bytes(raw)
    return {"key":f"price|{symbol}|{dd}","source_url":base,"path":str(p),"size_bytes":len(raw),"sha256":actual,"expected_sha256":expected,"reused":False}
def csvrows(p):
    with zipfile.ZipFile(p) as z:
        names=[n for n in z.namelist() if n.endswith(".csv")]
        if len(names)!=1:raise ValueError("CSV_SHAPE")
        return list(csv.reader(io.StringIO(z.read(names[0]).decode())))
def funding_gateway(symbol,end_date):
    start_ms=int(datetime.combine(START,datetime.min.time(),tzinfo=timezone.utc).timestamp()*1000)
    end_exclusive=int(datetime.combine(end_date+timedelta(days=1),datetime.min.time(),tzinfo=timezone.utc).timestamp()*1000)
    base=f"https://api-dev.pipai.org/funding/rates/{symbol}/history"
    url=base+"?"+urlencode({"start_time":start_ms,"end_time":end_exclusive-1,"limit":1000})
    raw=fetch(url);obj=json.loads(raw.decode("utf-8"))
    if not isinstance(obj,list):raise ValueError(f"FUNDING_SCHEMA:{symbol}")
    rows=[]
    for item in obj:
        ft=item.get("fundingTime",item.get("funding_time")); fr=item.get("fundingRate",item.get("funding_rate"))
        if ft is None or fr is None:raise ValueError(f"FUNDING_RECORD_SCHEMA:{symbol}")
        if start_ms<=int(ft)<end_exclusive:
            rows.append({"symbol":symbol,"fundingTime":int(ft),"fundingRate":float(fr)})
    rows=sorted(rows,key=lambda r:r["fundingTime"])
    if not rows:raise ValueError(f"FUNDING_EMPTY:{symbol}")
    canonical=json.dumps(rows,sort_keys=True,separators=(",",":")).encode()+b"\n"
    p=ROOT/"funding_gateway"/f"{symbol}-2026-09-forward.json";p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(canonical)
    return rows,{"key":f"funding|{symbol}|{end_date}","source_url":base,"request_scope":{"start_time":start_ms,"end_time":end_exclusive-1,"limit":1000},"sha256":sha(canonical),"size_bytes":len(canonical)}
END=datetime.now(timezone.utc).date()-timedelta(days=1)
if END<START:
    status={"protocol":"HARMONY-HOLDOUT-PROTOCOL-003","status":"NO_ELAPSED_DATA","observations":0,"minimum_required":MIN_OBS}
    (OUT/"gate-status.json").write_text(json.dumps(status,sort_keys=True,indent=2)+"\n");print(json.dumps(status));raise SystemExit(0)

price={s:{} for s in SYMBOLS};funding={s:{} for s in SYMBOLS};price_files=[];funding_files=[]
for s in SYMBOLS:
    funding[s],fm=funding_gateway(s,END);funding_files.append(fm)
    for dd in days(START,END):
        pf=daily_price(s,dd);price_files.append(pf)
        for r in csvrows(Path(pf["path"])):
            if r and r[0].isdigit():price[s][datetime.fromtimestamp(int(r[0])/1000,timezone.utc).date()]=float(r[4])

price_common=sorted(set.intersection(*(set(price[s]) for s in SYMBOLS)));price_common=[d for d in price_common if START<=d<=END]
complete_common=[d for d in price_common if all(funding[s] and any(datetime.fromtimestamp(int(r["fundingTime"])/1000,timezone.utc).date()==d for r in funding[s]) for s in SYMBOLS)]
contiguous=[]
for d in complete_common:
    if not contiguous:
        if d==START: contiguous=[d]
        continue
    if (d-contiguous[-1]).days==1:contiguous.append(d)
    else:break
common=contiguous
if len(common)<MIN_OBS:
    status={"protocol":"HARMONY-HOLDOUT-PROTOCOL-003","status":"ACCRUING_ELAPSED_DATA","as_of":END.isoformat(),"common_observations":len(common),"minimum_required":MIN_OBS,"common_start":common[0].isoformat() if common else None,"common_end":common[-1].isoformat() if common else None,"all_dates_complete":True if common else False}
    (OUT/"gate-status.json").write_text(json.dumps(status,sort_keys=True,indent=2)+"\n");print(json.dumps(status));raise SystemExit(0)

holdout=common[:MIN_OBS]; end=holdout[-1]
manifest={"protocol":"HARMONY-HOLDOUT-PROTOCOL-003","start":START.isoformat(),"end":end.isoformat(),"observations":MIN_OBS,"as_of":END.isoformat(),"symbols":SYMBOLS,"price_files":sorted(price_files,key=lambda x:x["key"]),"funding_files":sorted(funding_files,key=lambda x:x["key"]),"frontier_digest":FRONTIER,"candidate_ids":CAND,"candidate_scope_digest":CANDIDATE_SCOPE_DIGEST}
mb=json.dumps(manifest,sort_keys=True,indent=2).encode()+b"\n";msha=sha(mb);(OUT/"input-manifest.json").write_bytes(mb)

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
summary={"protocol":"HARMONY-HOLDOUT-PROTOCOL-003","status":"EXECUTED","start":START.isoformat(),"end":end.isoformat(),"observations":MIN_OBS,"input_manifest_sha256":msha,"benchmarks":benchmarks,"candidates":{}}
for cid in CAND:
    r={"candidate_id":cid,"input_manifest_sha256":msha,"frontier_digest":FRONTIER,"candidate_scope_digest":CANDIDATE_SCOPE_DIGEST,"benchmarks":benchmarks,"base":sim(cid,1.0),"cost_stress":{f"{m:.1f}x":sim(cid,m) for m in (1,1.5,2)},"holdout_released_to_selection":False}
    raw=json.dumps(r,sort_keys=True,indent=2).encode()+b"\n"; rsha=sha(raw); (OUT/f"{cid}.json").write_bytes(raw)
    summary["candidates"][cid]={"result_sha256":rsha,"base":r["base"],"cost_stress":r["cost_stress"]}
(OUT/"summary.json").write_text(json.dumps(summary,sort_keys=True,indent=2)+"\n"); print(json.dumps(summary,sort_keys=True,indent=2))
