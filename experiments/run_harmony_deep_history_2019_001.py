import csv, hashlib, io, json, zipfile
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

SYMBOLS=["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
START=date(2019,1,1); END=date(2025,10,31)
ROOT=Path("data/cache/binance/futures_um/deep_history_2019")
OUT=Path("artifacts/HARMONY-DEEP-HISTORY-2019-001"); OUT.mkdir(parents=True,exist_ok=True)
KLINE_BASE="https://data.binance.vision/data/futures/um/monthly/klines"

def fetch(url):
    req=Request(url,headers={"User-Agent":"Harmony/DEEP-HISTORY-2019-001"})
    with urlopen(req,timeout=120) as r:return r.read()

def sha(b): return hashlib.sha256(b).hexdigest()

def months(a,b):
    y,m=a.year,a.month; out=[]
    while (y,m)<=(b.year,b.month):
        out.append((y,m)); m+=1
        if m==13:y,m=y+1,1
    return out

def parse_zip(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names=[n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(names)!=1: raise ValueError("ARCHIVE_SHAPE")
        if z.testzip() is not None: raise ValueError("ARCHIVE_CRC")
        return list(csv.reader(io.StringIO(z.read(names[0]).decode("utf-8"))))

def price_month(symbol,y,m):
    ym=f"{y:04d}-{m:02d}"; fn=f"{symbol}-1d-{ym}.zip"
    url=f"{KLINE_BASE}/{symbol}/1d/{fn}"; p=ROOT/"klines"/symbol/"1d"/fn
    p.parent.mkdir(parents=True,exist_ok=True)
    try:
        try:
            expected=fetch(url+".CHECKSUM").decode("utf-8","replace").strip().split()[0].lower()
        except HTTPError as e:
            if e.code==404: return {"status":"unavailable","reason":"checksum_404","symbol":symbol,"month":ym,"url":url}
            raise
        if p.exists(): raw=p.read_bytes(); reused=True
        else: raw=fetch(url); p.write_bytes(raw); reused=False
        actual=sha(raw)
        if actual!=expected:
            p.unlink(missing_ok=True); raise ValueError(f"CHECKSUM_MISMATCH:{fn}")
        rows=parse_zip(raw)
        dates=[]; closes={}
        for r in rows:
            if r and r[0].isdigit():
                if len(r)<6: raise ValueError(f"KLINE_SCHEMA:{fn}")
                d=datetime.fromtimestamp(int(r[0])/1000,timezone.utc).date()
                dates.append(d.isoformat()); closes[d.isoformat()]=float(r[4])
        if dates!=sorted(dates) or len(dates)!=len(set(dates)): raise ValueError(f"KLINE_ORDER:{fn}")
        return {"status":"materialized","symbol":symbol,"month":ym,"url":url,"path":str(p),
                "sha256":actual,"expected_sha256":expected,"rows":len(dates),
                "first_date":dates[0] if dates else None,"last_date":dates[-1] if dates else None,
                "reused":reused,"closes":closes}
    except HTTPError as e:
        if e.code==404: return {"status":"unavailable","reason":"archive_404","symbol":symbol,"month":ym,"url":url}
        raise

def funding_history(symbol):
    base=f"https://api-dev.pipai.org/funding/rates/{symbol}/history"
    start_ms=int(datetime.combine(START,datetime.min.time(),tzinfo=timezone.utc).timestamp()*1000)
    end_ms=int(datetime.combine(END+timedelta(days=1),datetime.min.time(),tzinfo=timezone.utc).timestamp()*1000)-1
    cursor=start_ms; rows=[]; requests=[]; seen=set(); page=0
    try:
        while cursor<=end_ms:
            page+=1
            url=base+"?"+urlencode({"start_time":cursor,"end_time":end_ms,"limit":1000})
            raw=fetch(url); response_sha=sha(raw); obj=json.loads(raw.decode("utf-8"))
            if not isinstance(obj,list): raise ValueError(f"FUNDING_SCHEMA:{symbol}")
            requests.append({"page":page,"url":url,"sha256":response_sha,"byte_count":len(raw),"record_count":len(obj)})
            if not obj: break
            ordered=sorted(obj,key=lambda x:int(x.get("fundingTime",x.get("funding_time"))))
            for item in ordered:
                ft=int(item.get("fundingTime",item.get("funding_time"))); fr=float(item.get("fundingRate",item.get("funding_rate")))
                if not (start_ms<=ft<=end_ms): continue
                if ft in seen: raise ValueError(f"FUNDING_DUPLICATE:{symbol}:{ft}")
                seen.add(ft); rows.append({"symbol":symbol,"fundingTime":ft,"fundingRate":fr})
            last=max(int(x.get("fundingTime",x.get("funding_time"))) for x in ordered)
            if last>=end_ms or len(ordered)<1000: break
            cursor=last+1
        rows.sort(key=lambda x:x["fundingTime"])
        canonical=json.dumps(rows,sort_keys=True,separators=(",",":")).encode()+b"\n"
        p=ROOT/"funding_gateway"/f"{symbol}-2019-2025-10.json"; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(canonical)
        return {"status":"materialized","symbol":symbol,"url":base,"path":str(p),"sha256":sha(canonical),
                "row_count":len(rows),"first_funding":datetime.fromtimestamp(rows[0]["fundingTime"]/1000,timezone.utc).date().isoformat() if rows else None,
                "last_funding":datetime.fromtimestamp(rows[-1]["fundingTime"]/1000,timezone.utc).date().isoformat() if rows else None,
                "requests":requests}
    except Exception as exc:
        return {"status":"unavailable","symbol":symbol,"url":base,"reason":f"{type(exc).__name__}:{exc}","requests":requests}

price_meta=[]; price_maps={s:{} for s in SYMBOLS}; funding_meta=[]
for s in SYMBOLS:
    for y,m in months(START,END):
        r=price_month(s,y,m); price_meta.append({k:v for k,v in r.items() if k!="closes"})
        if r["status"]=="materialized": price_maps[s].update(r["closes"])
    funding_meta.append(funding_history(s))

common_dates=sorted(set.intersection(*(set(price_maps[s]) for s in SYMBOLS))) if all(price_maps[s] for s in SYMBOLS) else []
common_dates=[d for d in common_dates if START.isoformat()<=d<=END.isoformat()]

panel=OUT/"price-panel.csv"
with panel.open("w",newline="",encoding="utf-8") as f:
    w=csv.writer(f); w.writerow(["date"]+[s+"_close" for s in SYMBOLS])
    for d in common_dates: w.writerow([d]+[f"{price_maps[s][d]:.17g}" for s in SYMBOLS])
panel_sha=sha(panel.read_bytes())

coverage={}
for s in SYMBOLS:
    sm=[x for x in price_meta if x["symbol"]==s and x["status"]=="materialized"]
    fm=next(x for x in funding_meta if x["symbol"]==s)
    coverage[s]={
        "price_materialized_months":len(sm),
        "price_first_date":min((x["first_date"] for x in sm if x["first_date"]),default=None),
        "price_last_date":max((x["last_date"] for x in sm if x["last_date"]),default=None),
        "funding_status":fm["status"],
        "funding_first_date":fm.get("first_funding"),
        "funding_last_date":fm.get("last_funding"),
        "funding_rows":fm.get("row_count",0),
    }

manifest={
    "protocol":"HARMONY-DEEP-HISTORY-2019-001",
    "requested_acquisition_start":START.isoformat(),
    "requested_acquisition_end":END.isoformat(),
    "symbols":SYMBOLS,
    "price_source":KLINE_BASE,
    "funding_source":"https://api-dev.pipai.org/funding/rates/{symbol}/history",
    "common_price_panel":{"rows":len(common_dates),"start":common_dates[0] if common_dates else None,"end":common_dates[-1] if common_dates else None},
    "price_files":price_meta,
    "funding_files":funding_meta,
    "normalized_panel_sha256":panel_sha,
}
mb=json.dumps(manifest,sort_keys=True,indent=2).encode()+b"\n"
(OUT/"manifest.json").write_bytes(mb)
(OUT/"coverage.json").write_text(json.dumps(coverage,sort_keys=True,indent=2)+"\n",encoding="utf-8")
prov={
    "protocol":"HARMONY-DEEP-HISTORY-2019-001",
    "requested_start_materialized_boundary":START.isoformat(),
    "no_pre_inception_synthesis":True,
    "no_forward_fill":True,
    "no_interpolation":True,
    "common_panel_exact_intersection":True,
    "manifest_sha256":sha(mb),
    "normalized_panel_sha256":panel_sha,
}
(OUT/"provenance.json").write_text(json.dumps(prov,sort_keys=True,indent=2)+"\n",encoding="utf-8")
status={
    "protocol":"HARMONY-DEEP-HISTORY-2019-001",
    "status":"MATERIALIZED" if any(x["status"]=="materialized" for x in price_meta) else "NO_DATA",
    "requested_start":START.isoformat(),"requested_end":END.isoformat(),
    "common_price_start":common_dates[0] if common_dates else None,
    "common_price_end":common_dates[-1] if common_dates else None,
    "common_price_rows":len(common_dates),
    "acquisition_scope_frozen":True,
    "strategy_execution_authorized":False,
}
(OUT/"status.json").write_text(json.dumps(status,sort_keys=True,indent=2)+"\n")
print(json.dumps(status,sort_keys=True,indent=2))
