from __future__ import annotations
import concurrent.futures, csv, hashlib, io, json, math, statistics, urllib.request, zipfile
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd

from ta import add_all_ta_features
from ta.trend import ADXIndicator, AroonIndicator, CCIIndicator, EMAIndicator, MACD
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.volatility import BollingerBands, KeltnerChannel, AverageTrueRange
from ta.volume import VolumeWeightedAveragePrice

ROOT=Path(__file__).resolve().parents[1]
RAW=ROOT/"data/cache/harmony_gateway_v8/raw_4h_fast"
IND=ROOT/"data/cache/harmony_gateway_v8/indicators_4h_fast"
OUT=ROOT/"artifacts/HARMONY-TECH-4H-027"
ASSETS=["JTOUSDT","PYTHUSDT","WIFUSDT","SEIUSDT","TIAUSDT","ENAUSDT","ORDIUSDT","JUPUSDT"]
START="2024-01-01";END="2025-10-31";DISCOVERY_END="2025-02-28";OOS_START="2025-03-01"
BASE_URL="https://data.binance.vision/data/futures/um/monthly/klines"

def sha(b):
    if isinstance(b, Path):
        b = b.read_bytes()
    return hashlib.sha256(b).hexdigest()

def months():
    out=[];y,m=2023,1
    while (y,m)<=(2025,10):
        out.append((y,m));m+=1
        if m==13:y,m=y+1,1
    return out

def download(pair,ym):
    y,m=ym;fn=f"{pair}-4h-{y}-{m:02d}.zip";url=f"{BASE_URL}/{pair}/4h/{fn}"
    req=urllib.request.Request(url,headers={"User-Agent":"Harmony-Tech-4H-025"})
    try:
        with urllib.request.urlopen(req,timeout=60) as r: raw=r.read()
        return {"ym":ym,"raw":raw,"sha":sha(raw),"missing":False}
    except Exception as e:
        return {"ym":ym,"raw":None,"sha":None,"missing":True,"error":str(e)}

def materialize_raw(pair):
    pair_dir=RAW/pair;pair_dir.mkdir(parents=True,exist_ok=True)
    entries=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for item in ex.map(lambda ym:download(pair,ym),months()):
            ym=item["ym"]
            if item["missing"]:
                entries.append({"year":ym[0],"month":ym[1],"status":"missing","error":item["error"]})
                continue
            path=pair_dir/f"{pair}-4h-{ym[0]}-{ym[1]:02d}.zip"
            path.write_bytes(item["raw"]);entries.append({"year":ym[0],"month":ym[1],"status":"downloaded","sha256":item["sha"],"bytes":len(item["raw"])})
    return entries

def read_asset(pair):
    rows=[]
    for p in sorted((RAW/pair).glob(f"{pair}-4h-*.zip")):
        with zipfile.ZipFile(p) as z:
            names=[n for n in z.namelist() if n.lower().endswith(".csv")]
            if len(names)!=1:raise RuntimeError(f"unexpected archive {p}")
            for r in csv.reader(z.read(names[0]).decode("utf-8","replace").splitlines()):
                if r and r[0].isdigit() and len(r)>=11:
                    ts=int(r[0]);dt=datetime.fromtimestamp(ts/1000,timezone.utc)
                    d=dt.date().isoformat()
                    if START<=d<=END:
                        rows.append([dt,float(r[1]),float(r[2]),float(r[3]),float(r[4]),float(r[5]),float(r[7]),float(r[9]),float(r[10]),int(r[8])])
    rows=sorted({int(x[0].timestamp()*1000):x for x in rows}.values(),key=lambda x:x[0])
    return rows

def build_indicators(pair,rows):
    if len(rows)<300:raise RuntimeError(f"{pair} insufficient 4h bars: {len(rows)}")
    df=pd.DataFrame(rows,columns=["ts","open","high","low","close","volume","quote_volume","taker_buy_base","taker_buy_quote","trades"])
    df["timestamp"]=pd.to_datetime(df["ts"],unit="s",utc=True)
    # Full library warehouse.
    feat=add_all_ta_features(df.copy(),open="open",high="high",low="low",close="close",volume="volume",fillna=False)
    # Explicit research features with stable names.
    ema20=EMAIndicator(df["close"],20).ema_indicator()
    ema50=EMAIndicator(df["close"],50).ema_indicator()
    ema200=EMAIndicator(df["close"],200).ema_indicator()
    atr=AverageTrueRange(df["high"],df["low"],df["close"],14).average_true_range()
    macd=MACD(df["close"],26,12,9)
    rsi=RSIIndicator(df["close"],14).rsi()
    st=StochasticOscillator(df["high"],df["low"],df["close"],14,3)
    adx=ADXIndicator(df["high"],df["low"],df["close"],14)
    ar=AroonIndicator(df["high"],df["low"],window=25)
    cci=CCIIndicator(df["high"],df["low"],df["close"],20).cci()
    bb=BollingerBands(df["close"],20,2)
    kc=KeltnerChannel(df["high"],df["low"],df["close"],20)
    vwap=VolumeWeightedAveragePrice(df["high"],df["low"],df["close"],df["volume"],20).volume_weighted_average_price()
    feat["ema20"]=ema20;feat["ema50"]=ema50;feat["ema200"]=ema200
    feat["ema20_slope_6"]=ema20/ema20.shift(6)-1
    feat["atr14_pct"]=atr/df["close"]
    feat["macd_line"]=macd.macd();feat["macd_signal"]=macd.macd_signal();feat["macd_hist"]=macd.macd_diff()
    feat["rsi14"]=rsi;feat["stoch_k"]=st.stoch();feat["stoch_d"]=st.stoch_signal()
    feat["adx14"]=adx.adx();feat["di_plus14"]=adx.adx_pos();feat["di_minus14"]=adx.adx_neg()
    feat["aroon_up25"]=ar.aroon_up();feat["aroon_down25"]=ar.aroon_down()
    feat["cci20"]=cci;feat["bb_mid20"]=bb.bollinger_mavg();feat["bb_pband20"]=bb.bollinger_pband();feat["bb_wband20"]=bb.bollinger_wband()
    feat["kc_mid20"]=kc.keltner_channel_mband();feat["kc_high20"]=kc.keltner_channel_hband();feat["kc_low20"]=kc.keltner_channel_lband()
    feat["vwap20"]=vwap
    feat["donchian20_high"]=df["high"].rolling(20).max();feat["donchian20_low"]=df["low"].rolling(20).min()
    feat["volume_z20"]=(df["volume"]-df["volume"].rolling(20).mean())/(df["volume"].rolling(20).std()+1e-12)
    feat["return_1"]=df["close"].pct_change();feat["return_6"]=df["close"].pct_change(6)
    # retain source fields plus features; normalize non-finite values to NaN.
    source_cols=["timestamp","open","high","low","close","volume","quote_volume","taker_buy_base","taker_buy_quote","trades"]
    keep=source_cols+[c for c in feat.columns if c not in source_cols and c!="ts"]
    out=feat[keep].copy()
    out.replace([float("inf"),float("-inf")],float("nan"),inplace=True)
    path=IND/f"{pair}_4h_indicators.csv";path.parent.mkdir(parents=True,exist_ok=True)
    out.to_csv(path,index=False)
    return path,out

def valid_num(row,key):
    v=row.get(key)
    try:return float(v) if v not in ("",None) and math.isfinite(float(v)) else None
    except:return None

def signal(cid,df,i):
    if i<60:return 0
    c=float(df["close"].iloc[i]);p1=float(df["close"].iloc[i-1]);p2=float(df["close"].iloc[i-2])
    e20=float(df["ema20"].iloc[i]);e50=float(df["ema50"].iloc[i]);slope=float(df["ema20_slope_6"].iloc[i])
    trend=1 if e20>e50 and slope>0 else -1 if e20<e50 and slope<0 else 0
    if trend==0:return 0
    # Common short-pullback diagnostics.
    pull_long=(float(df["close"].iloc[i-1])<float(df["close"].iloc[i-2]))
    pull_short=(float(df["close"].iloc[i-1])>float(df["close"].iloc[i-2]))
    prev_hi=max(float(df["high"].iloc[i-1]),float(df["high"].iloc[i-2]));prev_lo=min(float(df["low"].iloc[i-1]),float(df["low"].iloc[i-2]))
    if cid=="TC01":
        return 1 if trend==1 and pull_long and min(float(df["low"].iloc[i-2]),float(df["low"].iloc[i-1]))>e50 and c>prev_hi else -1 if trend==-1 and pull_short and max(float(df["high"].iloc[i-2]),float(df["high"].iloc[i-1]))<e50 and c<prev_lo else 0
    if cid=="TC02":
        adx=float(df["adx14"].iloc[i]);dp=float(df["di_plus14"].iloc[i]);dm=float(df["di_minus14"].iloc[i])
        return 1 if trend==1 and adx>=20 and dp>dm and pull_long and c>prev_hi else -1 if trend==-1 and adx>=20 and dm>dp and pull_short and c<prev_lo else 0
    if cid=="TC03":
        rsi=float(df["rsi14"].iloc[i]);rsi_prev=float(df["rsi14"].iloc[i-1])
        return 1 if trend==1 and pull_long and rsi_prev<50<=rsi else -1 if trend==-1 and pull_short and rsi_prev>50>=rsi else 0
    if cid=="TC04":
        h=float(df["macd_hist"].iloc[i]);hp=float(df["macd_hist"].iloc[i-1])
        return 1 if trend==1 and pull_long and hp<=0<h else -1 if trend==-1 and pull_short and hp>=0>h else 0
    if cid=="TC05":
        mid=float(df["bb_mid20"].iloc[i]);midp=float(df["bb_mid20"].iloc[i-1]);cp=float(df["close"].iloc[i-1])
        return 1 if trend==1 and cp<midp and c>=mid else -1 if trend==-1 and cp>midp and c<=mid else 0
    if cid=="TC06":
        k=float(df["stoch_k"].iloc[i]);d=float(df["stoch_d"].iloc[i]);kp=float(df["stoch_k"].iloc[i-1]);dp=float(df["stoch_d"].iloc[i-1])
        return 1 if trend==1 and kp<=dp and k>d and k<80 else -1 if trend==-1 and kp>=dp and k<d and k>20 else 0
    if cid=="TC07":
        dh=float(df["donchian20_high"].iloc[i-1]);dl=float(df["donchian20_low"].iloc[i-1])
        return 1 if trend==1 and pull_long and c>dh else -1 if trend==-1 and pull_short and c<dl else 0
    if cid=="TC08":
        bw=float(df["bb_wband20"].iloc[i]);med=float(df["bb_wband20"].rolling(60).median().iloc[i-1])
        km=float(df["kc_mid20"].iloc[i]);return 1 if trend==1 and bw<med and c>km else -1 if trend==-1 and bw<med and c<km else 0
    if cid=="TC09":
        up=float(df["aroon_up25"].iloc[i]);dn=float(df["aroon_down25"].iloc[i])
        return 1 if trend==1 and up>70 and up>dn and pull_long else -1 if trend==-1 and dn>70 and dn>up and pull_short else 0
    if cid=="TC10":
        ci=float(df["cci20"].iloc[i]);cip=float(df["cci20"].iloc[i-1])
        return 1 if trend==1 and pull_long and cip<=0<ci else -1 if trend==-1 and pull_short and cip>=0>ci else 0
    raise KeyError(cid)

def backtest(cid,assets_data,cost_bps,start,end,hold_bars=6):
    # Event-driven, close-t signal -> next-open entry. Timestamp indexes are precomputed to avoid quadratic lookups.
    timelines={a:list(df["timestamp"]) for a,df in assets_data.items()}
    ts_maps={a:{ts:i for i,ts in enumerate(timeline)} for a,timeline in timelines.items()}
    timeline=sorted(set().union(*[set(timeline) for timeline in timelines.values()]))
    equity=1.0;curve=[];trades=[];cost=cost_bps/10000
    active={}
    for ts in timeline:
        # Exit due to time limit.
        for pos_id,pos in list(active.items()):
            if ts>=pos["exit_ts"]:
                df=assets_data[pos["asset"]];mp=ts_maps[pos["asset"]].get(ts)
                if mp is not None:
                    px=float(df["open"].iloc[mp]);ret=(px/pos["entry_px"]-1)*pos["side"]
                    equity*=1+ret/max(1,len(active));equity*=max(0,1-cost)
                    trades.append({"asset":pos["asset"],"side":pos["side"],"entry":pos["entry_ts"].isoformat(),"exit":ts.isoformat(),"ret":ret})
                del active[pos_id]
        # Generate signals from the previous completed bar for each asset.
        for asset,df in assets_data.items():
            mp=ts_maps[asset].get(ts)
            if mp is None or mp<61:continue
            if any(p["asset"]==asset for p in active.values()):continue
            sig=signal(cid,df,mp-1)
            if sig==0:continue
            px=float(df["open"].iloc[mp])
            equity*=max(0,1-cost)
            exit_idx=min(mp+hold_bars,len(df)-1)
            active[f"{asset}-{ts}"]={"asset":asset,"side":sig,"entry_px":px,"entry_ts":ts,"exit_ts":df["timestamp"].iloc[exit_idx]}
        curve.append((ts,equity))
    for pos in list(active.values()):
        df=assets_data[pos["asset"]];px=float(df["close"].iloc[-1])
        ret=(px/pos["entry_px"]-1)*pos["side"];equity*=1+ret/max(1,len(active));equity*=max(0,1-cost)
        trades.append({"asset":pos["asset"],"side":pos["side"],"entry":pos["entry_ts"].isoformat(),"exit":"forced","ret":ret})
    return curve,trades

def metrics(curve,start,end):
    xs=[v for ts,v in curve if start<=ts.date().isoformat()<=end]
    if len(xs)<3:return {"return":0.0,"sharpe":0.0,"mdd":0.0,"observations":len(xs)}
    rr=[xs[i]/xs[i-1]-1 for i in range(1,len(xs))];sd=statistics.stdev(rr) if len(rr)>1 else 0
    sh=statistics.mean(rr)/sd*math.sqrt(365.25*6) if sd else 0
    pk=xs[0];mdd=0
    for x in xs:pk=max(pk,x);mdd=min(mdd,x/pk-1)
    return {"return":xs[-1]/xs[0]-1,"sharpe":sh,"mdd":mdd,"observations":len(xs)}

def main():
    OUT.mkdir(parents=True,exist_ok=True);RAW.mkdir(parents=True,exist_ok=True);IND.mkdir(parents=True,exist_ok=True)
    manifest={"batch_id":"HARMONY-TECH-4H-027","assets":{},"library":"ta==0.11.0"}
    assets_data={}
    for a in ASSETS:
        entries=materialize_raw(a);rows=read_asset(a)
        path,df=build_indicators(a,rows);assets_data[a]=df
        manifest["assets"][a]={"raw_months":len(entries),"downloaded_months":sum(e["status"]=="downloaded" for e in entries),
                               "missing_months":[e for e in entries if e["status"]=="missing"],"bars":len(rows),
                               "indicator_columns":len(df.columns),"indicator_sha256":sha(path.read_bytes())}
    (OUT/"INDICATOR-WAREHOUSE-MANIFEST.json").write_text(json.dumps(manifest,sort_keys=True,indent=2)+"\n")
    names=[f"TC0{i}" for i in range(1,10)]+["TC10"];results={};passed=[]
    for cid in names:
        costs={}
        for bps in (11,16.5,22):
            curve,trades=backtest(cid,assets_data,bps,"2023-01-01","2025-10-31")
            costs[str(bps)]={"discovery":metrics(curve,"2023-01-01",DISCOVERY_END),"oos":metrics(curve,OOS_START,END),"trades":len(trades)}
        g=costs["11"]["discovery"];g22=costs["22"]["discovery"]
        ok=costs["11"]["trades"]>=100 and g["return"]>0 and g22["return"]>0 and g["sharpe"]>=.50 and g22["sharpe"]>=.25
        results[cid]={"status":"CHEAP_PASS" if ok else "CHEAP_FAIL","costs":costs}
        if ok:passed.append(cid)
    passed.sort(key=lambda c:(results[c]["costs"]["22"]["discovery"]["sharpe"],c),reverse=True)
    selected=passed[:3];deep={};promoted=[]
    for cid in selected:
        d=results[cid]["costs"]
        # Already full OOS above; run an additional five-cost and two-half durability check.
        extra={}
        for bps in (11,16.5,22,27.5,33):
            curve,trades=backtest(cid,assets_data,bps,"2023-01-01","2025-10-31")
            extra[str(bps)]={"oos":metrics(curve,OOS_START,END),"trades":len(trades)}
        base_curve,_=backtest(cid,assets_data,11,"2023-01-01","2025-10-31")
        oos=[(ts,v) for ts,v in base_curve if ts.date().isoformat()>=OOS_START]
        mid=len(oos)//2
        first=[(ts,v) for ts,v in oos[:mid]];second=[(ts,v) for ts,v in oos[mid:]]
        deep[cid]={"stress":extra,"oos_halves":{"first":metrics(first,OOS_START,OOS_START),"second":metrics(second,OOS_START,END)}}
        r11=extra["11"]["oos"];r22=extra["22"]["oos"];h=deep[cid]["oos_halves"]
        if r11["sharpe"]>=.90 and r22["sharpe"]>=.50 and h["first"]["sharpe"]>0 and h["second"]["sharpe"]>0:promoted.append(cid)
    result={"batch_id":"HARMONY-TECH-4H-025","manifest_sha256":sha((OUT/"INDICATOR-WAREHOUSE-MANIFEST.json").read_bytes()),
            "results":results,"passed_cheap":passed,"selected_for_deep":selected,"promoted_guarded":promoted,
            "integrity":{"parameter_search":False,"universe_search":False,"direction_search":False,"candidate_mutation":False,"holdout_access":False}}
    raw=json.dumps(result,sort_keys=True,indent=2).encode()
    (OUT/"HARMONY-TECH-4H-025-RESULT.json").write_bytes(raw)
    (OUT/"SUMMARY.json").write_text(json.dumps({"batch_id":result["batch_id"],"passed_cheap":passed,"selected_for_deep":selected,
        "promoted_guarded":promoted,"manifest_sha256":result["manifest_sha256"],"result_sha256":sha(OUT/"HARMONY-TECH-4H-025-RESULT.json")},sort_keys=True,indent=2)+"\n")
    print(json.dumps({"passed_cheap":passed,"selected_for_deep":selected,"promoted_guarded":promoted,"assets":len(ASSETS)},indent=2))

if __name__=="__main__":main()
