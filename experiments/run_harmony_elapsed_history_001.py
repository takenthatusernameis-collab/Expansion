import csv,hashlib,io,json,zipfile
from datetime import date,timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,urlopen

SYMBOLS=["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
START=date(2026,9,1); END=date(2026,9,29)
ROOT=Path("data/cache/binance/futures_um/elapsed_history_2026-09-daily")
OUT=Path("artifacts/HARMONY-ELAPSED-HISTORY-001"); OUT.mkdir(parents=True,exist_ok=True)

def fetch(url):
    req=Request(url,headers={"User-Agent":"Harmony/ELAPSED-HISTORY-001"})
    with urlopen(req,timeout=120) as r:return r.read()
def sha(b):return hashlib.sha256(b).hexdigest()
def days(a,b):
    cur=a
    while cur<=b:
        yield cur
        cur+=timedelta(days=1)
def ensure(symbol,kind,dd):
    if kind=="price":
        name=f"{symbol}-1d-{dd:%Y-%m-%d}.zip"
        rel=Path("klines")/symbol/"1d"/name
        base=f"https://data.binance.vision/data/futures/um/daily/klines/{symbol}/1d/{name}"
    else:
        name=f"{symbol}-fundingRate-{dd:%Y-%m-%d}.zip"
        rel=Path("fundingRate")/symbol/name
        base=f"https://data.binance.vision/data/futures/um/daily/fundingRate/{symbol}/{name}"
    p=ROOT/rel;p.parent.mkdir(parents=True,exist_ok=True)
    try: expected=sha(fetch(base+".CHECKSUM")).lower()
    except HTTPError as exc:
        if exc.code==404: raise FileNotFoundError(base+".CHECKSUM")
        raise
    if p.exists():
        raw=p.read_bytes(); actual=sha(raw)
        if actual==expected:
            return {"key":f"{symbol}|{kind}|{dd}","source_url":base,"path":str(p),"size_bytes":len(raw),"sha256":actual,"expected_sha256":expected,"reused":True}
        p.unlink()
    raw=fetch(base);actual=sha(raw)
    if actual!=expected:
        p.unlink(missing_ok=True);raise ValueError(f"CHECKSUM_MISMATCH:{base}")
    p.write_bytes(raw)
    return {"key":f"{symbol}|{kind}|{dd}","source_url":base,"path":str(p),"size_bytes":len(raw),"sha256":actual,"expected_sha256":expected,"reused":False}

def rows(p):
    with zipfile.ZipFile(p) as z:
        names=[n for n in z.namelist() if n.endswith(".csv")]
        if len(names)!=1:raise ValueError(f"ARCHIVE_SHAPE:{p}")
        return list(csv.reader(io.StringIO(z.read(names[0]).decode())))

files=[]
for s in SYMBOLS:
    for dd in days(START,END):
        for kind in ("price","funding"):
            try: files.append(ensure(s,kind,dd))
            except FileNotFoundError as exc:
                status={"protocol":"HARMONY-ELAPSED-HISTORY-001","status":"BLOCKED_MISSING_DAILY_ARCHIVE","missing_source":str(exc),"completed_days":0}
                (OUT/"status.json").write_text(json.dumps(status,sort_keys=True,indent=2)+"\n");print(json.dumps(status));raise SystemExit(0)

manifest={"dataset_id":"HARMONY-USDM-DAILY-FUNDING-2026-09-ELAPSED","protocol":"HARMONY-ELAPSED-HISTORY-001","start":START.isoformat(),"end":END.isoformat(),"symbols":SYMBOLS,"timeframe":"1d","files":sorted(files,key=lambda x:x["key"])}
mb=json.dumps(manifest,sort_keys=True,indent=2).encode()+b"\n";msha=sha(mb);(OUT/"manifest.json").write_bytes(mb)

panel=[]
for dd in days(START,END):
    for s in SYMBOLS:
        kp=ROOT/"klines"/s/"1d"/f"{s}-1d-{dd:%Y-%m-%d}.zip"
        fp=ROOT/"fundingRate"/s/f"{s}-fundingRate-{dd:%Y-%m-%d}.zip"
        kr=rows(kp); fr=rows(fp)
        price_close=float(kr[-1][4]) if kr else None
        header={k.strip():i for i,k in enumerate(fr[0])}
        rates=[float(r[header["last_funding_rate"]]) for r in fr[1:] if r]
        panel.append({"date":dd.isoformat(),"symbol":s,"close":price_close,"funding_obs":len(rates),"funding_mean":sum(rates)/len(rates) if rates else None})
pb=json.dumps(panel,sort_keys=True,separators=(",",":")).encode()+b"\n";psha=sha(pb);(OUT/"normalized-panel.json").write_bytes(pb)
status={"protocol":"HARMONY-ELAPSED-HISTORY-001","status":"MATERIALIZED","dataset_id":"HARMONY-USDM-DAILY-FUNDING-2026-09-ELAPSED","start":START.isoformat(),"end":END.isoformat(),"days":(END-START).days+1,"symbols":len(SYMBOLS),"file_count":len(files),"manifest_sha256":msha,"normalized_panel_sha256":psha,"oos_claim_authorized":False,"candidate_execution_authorized":False}
(OUT/"status.json").write_text(json.dumps(status,sort_keys=True,indent=2)+"\n");print(json.dumps(status,sort_keys=True,indent=2))
