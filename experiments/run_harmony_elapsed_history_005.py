import csv,hashlib,io,json,zipfile
from datetime import date,datetime,timezone,timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request,urlopen

SYMBOLS=["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
START=date(2026,9,1); END=date(2026,9,29)
START_MS=int(datetime(2026,9,1,tzinfo=timezone.utc).timestamp()*1000)
END_EXCLUSIVE_MS=int(datetime(2026,9,30,tzinfo=timezone.utc).timestamp()*1000)
ROOT=Path("data/cache/binance/futures_um/elapsed_history_2026-09-daily-v5")
OUT=Path("artifacts/HARMONY-ELAPSED-HISTORY-005"); OUT.mkdir(parents=True,exist_ok=True)

def fetch(url):
    req=Request(url,headers={"User-Agent":"Harmony/ELAPSED-HISTORY-005"})
    with urlopen(req,timeout=120) as r:return r.read()
def sha(b):return hashlib.sha256(b).hexdigest()
def expected_checksum(url):
    try: raw=fetch(url+".CHECKSUM").decode("utf-8","replace").strip().split()
    except HTTPError as exc:
        if exc.code==404:return None
        raise
    if not raw:raise ValueError("EMPTY_CHECKSUM:"+url)
    token=raw[0].lower()
    if len(token)!=64:raise ValueError("INVALID_CHECKSUM_TOKEN:"+url)
    int(token,16);return token
def days():
    cur=START
    while cur<=END:
        yield cur;cur+=timedelta(days=1)
def daily_price(symbol,dd):
    name=f"{symbol}-1d-{dd:%Y-%m-%d}.zip"
    base=f"https://data.binance.vision/data/futures/um/daily/klines/{symbol}/1d/{name}"
    p=ROOT/"klines"/symbol/"1d"/name;p.parent.mkdir(parents=True,exist_ok=True)
    expected=expected_checksum(base)
    if p.exists():
        raw=p.read_bytes();actual=sha(raw)
        if raw and (expected is None or actual==expected):
            return {"key":f"price|{symbol}|{dd}","source_url":base,"path":str(p),"size_bytes":len(raw),"sha256":actual,"expected_sha256":expected,"source_kind":"binance_daily_archive","reused":True}
        p.unlink()
    raw=fetch(base);actual=sha(raw)
    if not raw:raise ValueError("EMPTY_PRICE:"+base)
    if expected is not None and actual!=expected:raise ValueError("PRICE_CHECKSUM_MISMATCH:"+base)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if z.testzip() is not None:raise ValueError("PRICE_ZIP_CRC:"+base)
        names=[n for n in z.namelist() if n.endswith(".csv")]
        if len(names)!=1:raise ValueError("PRICE_ARCHIVE_SHAPE:"+base)
    p.write_bytes(raw)
    return {"key":f"price|{symbol}|{dd}","source_url":base,"path":str(p),"size_bytes":len(raw),"sha256":actual,"expected_sha256":expected,"source_kind":"binance_daily_archive","reused":False}

def funding_gateway(symbol):
    rows=[];cursor=START_MS;requests=[]
    while cursor<END_EXCLUSIVE_MS:
        params=urlencode({"start_time":cursor,"end_time":END_EXCLUSIVE_MS-1,"limit":1000})
        base=f"https://api-dev.pipai.org/funding/rates/{symbol}/history"
        url=base+"?"+params
        raw=fetch(url);raw_sha=sha(raw);obj=json.loads(raw.decode("utf-8"))
        if not isinstance(obj,list):raise ValueError(f"FUNDING_GATEWAY_SCHEMA:{symbol}")
        requests.append({"url":url,"sha256":raw_sha,"byte_count":len(raw),"record_count":len(obj)})
        if not obj:break
        ordered=[]
        for item in obj:
            ft=item.get("fundingTime",item.get("funding_time"))
            fr=item.get("fundingRate",item.get("funding_rate"))
            if ft is None or fr is None:raise ValueError(f"FUNDING_GATEWAY_RECORD_SCHEMA:{symbol}")
            ordered.append({"symbol":item.get("symbol",symbol),"fundingTime":int(ft),"fundingRate":str(fr),"markPrice":str(item.get("markPrice",item.get("mark_price",""))),"raw":item})
        ordered.sort(key=lambda x:x["fundingTime"])
        for item in ordered:
            if START_MS<=item["fundingTime"]<END_EXCLUSIVE_MS and item["symbol"]==symbol:
                rows.append({k:item[k] for k in ("symbol","fundingTime","fundingRate","markPrice")})
        last=ordered[-1]["fundingTime"]
        if last>=END_EXCLUSIVE_MS-1 or len(ordered)<1000:break
        cursor=last+1
    dedup={(r["symbol"],r["fundingTime"]):r for r in rows}
    rows=sorted(dedup.values(),key=lambda r:r["fundingTime"])
    if not rows:raise ValueError(f"FUNDING_GATEWAY_EMPTY:{symbol}")
    canonical=json.dumps(rows,sort_keys=True,separators=(",",":")).encode()+b"\n"
    p=ROOT/"funding_gateway"/f"{symbol}-2026-09.json";p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(canonical)
    return rows,{"key":f"funding|{symbol}|2026-09","source_url":f"https://api-dev.pipai.org/funding/rates/{symbol}/history","path":str(p),"size_bytes":len(canonical),"sha256":sha(canonical),"source_kind":"primit_funding_history_gateway","request_scope":{"start_time":START_MS,"end_time":END_EXCLUSIVE_MS-1,"limit":1000},"requests":requests}

def csvrows(p):
    with zipfile.ZipFile(p) as z:
        names=[n for n in z.namelist() if n.endswith(".csv")]
        if len(names)!=1:raise ValueError("CSV_SHAPE")
        return list(csv.reader(io.StringIO(z.read(names[0]).decode())))

price_files=[];funding_files=[];funding={}
for s in SYMBOLS:
    funding[s],fm=funding_gateway(s);funding_files.append(fm)
    for dd in days():price_files.append(daily_price(s,dd))

panel=[]
for dd in days():
    for s in SYMBOLS:
        kr=csvrows(ROOT/"klines"/s/"1d"/f"{s}-1d-{dd:%Y-%m-%d}.zip")
        if len(kr)<2 or len(kr[-1])<5:raise ValueError(f"PRICE_SCHEMA:{s}:{dd}")
        rates=[float(r["fundingRate"]) for r in funding[s] if datetime.fromtimestamp(int(r["fundingTime"])/1000,timezone.utc).date()==dd]
        if not rates:raise ValueError(f"FUNDING_GAP:{s}:{dd}")
        panel.append({"date":dd.isoformat(),"symbol":s,"close":float(kr[-1][4]),"funding_obs":len(rates),"funding_mean":sum(rates)/len(rates)})

manifest={"dataset_id":"HARMONY-USDM-DAILY-FUNDING-2026-09-ELAPSED","protocol":"HARMONY-ELAPSED-HISTORY-005","start":START.isoformat(),"end":END.isoformat(),"symbols":SYMBOLS,"timeframe":"1d","price_files":sorted(price_files,key=lambda x:x["key"]),"funding_files":sorted(funding_files,key=lambda x:x["key"])}
mb=json.dumps(manifest,sort_keys=True,indent=2).encode()+b"\n";msha=sha(mb);(OUT/"manifest.json").write_bytes(mb)
pb=json.dumps(panel,sort_keys=True,separators=(",",":")).encode()+b"\n";psha=sha(pb);(OUT/"normalized-panel.json").write_bytes(pb)
status={"protocol":"HARMONY-ELAPSED-HISTORY-005","status":"MATERIALIZED","dataset_id":"HARMONY-USDM-DAILY-FUNDING-2026-09-ELAPSED","start":START.isoformat(),"end":END.isoformat(),"days":29,"symbols":8,"panel_rows":len(panel),"manifest_sha256":msha,"normalized_panel_sha256":psha,"funding_source":"primit_public_history_gateway","price_source":"binance_daily_archive","all_dates_complete":True,"all_symbols_complete":True,"candidate_execution_authorized":False,"oos_claim_authorized":False}
(OUT/"status.json").write_text(json.dumps(status,sort_keys=True,indent=2)+"\n");print(json.dumps(status,sort_keys=True,indent=2))
