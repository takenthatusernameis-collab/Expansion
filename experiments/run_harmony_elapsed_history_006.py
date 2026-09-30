import csv,hashlib,io,json,re,zipfile
from datetime import date,datetime,timezone,timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,urlopen

SYMBOLS=["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
START=date(2026,9,1); END=date(2026,9,29)
ROOT=Path("data/cache/binance/futures_um/elapsed_history_2026-09-daily-v6")
OUT=Path("artifacts/HARMONY-ELAPSED-HISTORY-006"); OUT.mkdir(parents=True,exist_ok=True)

class TableParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.rows=[]; self.row=[]; self.cell=[]; self.in_tr=False; self.in_cell=False
    def handle_starttag(self,tag,attrs):
        if tag=="tr": self.in_tr=True; self.row=[]
        elif tag in ("td","th") and self.in_tr: self.in_cell=True; self.cell=[]
    def handle_data(self,data):
        if self.in_cell: self.cell.append(data)
    def handle_endtag(self,tag):
        if tag in ("td","th") and self.in_cell:
            self.row.append(" ".join("".join(self.cell).split())); self.in_cell=False
        elif tag=="tr" and self.in_tr:
            if self.row:self.rows.append(self.row)
            self.in_tr=False

def fetch(url):
    req=Request(url,headers={"User-Agent":"Harmony/ELAPSED-HISTORY-006"})
    with urlopen(req,timeout=120) as r:return r.read()
def sha(b):return hashlib.sha256(b).hexdigest()
def expected_checksum(url):
    try: raw=fetch(url+".CHECKSUM").decode("utf-8","replace").strip().split()
    except HTTPError as exc:
        if exc.code==404:return None
        raise
    if not raw:raise ValueError("EMPTY_CHECKSUM:"+url)
    return raw[0].lower()
def days():
    d=START
    while d<=END:
        yield d;d+=timedelta(days=1)

def daily_price(symbol,dd):
    name=f"{symbol}-1d-{dd:%Y-%m-%d}.zip"
    base=f"https://data.binance.vision/data/futures/um/daily/klines/{symbol}/1d/{name}"
    p=ROOT/"klines"/symbol/"1d"/name;p.parent.mkdir(parents=True,exist_ok=True)
    expected=expected_checksum(base)
    if p.exists():
        raw=p.read_bytes();actual=sha(raw)
        if raw and (expected is None or actual==expected):return {"key":f"price|{symbol}|{dd}","url":base,"path":str(p),"size":len(raw),"sha256":actual,"expected_sha256":expected,"reused":True}
        p.unlink()
    raw=fetch(base);actual=sha(raw)
    if not raw:raise ValueError("EMPTY_PRICE:"+base)
    if expected is not None and actual!=expected:raise ValueError("PRICE_CHECKSUM_MISMATCH:"+base)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if z.testzip() is not None:raise ValueError("PRICE_ZIP_CRC:"+base)
        names=[n for n in z.namelist() if n.endswith(".csv")]
        if len(names)!=1:raise ValueError("PRICE_ARCHIVE_SHAPE:"+base)
    p.write_bytes(raw)
    return {"key":f"price|{symbol}|{dd}","url":base,"path":str(p),"size":len(raw),"sha256":actual,"expected_sha256":expected,"reused":False}

def funding_page(symbol):
    url=f"https://pandabull.io/perpetuals-funding/binance/{symbol}"
    raw=fetch(url);page_sha=sha(raw);parser=TableParser();parser.feed(raw.decode("utf-8","replace"))
    rows=[]
    for row in parser.rows:
        if len(row)>=4 and re.match(r"^2026-\d{2}-\d{2} \d{2}:\d{2} UTC$",row[0]):
            try:
                dd=datetime.strptime(row[0],"%Y-%m-%d %H:%M UTC").date()
                if START<=dd<=END:
                    rate=float(row[1].rstrip("%"))/100.0
                    interval=int(float(row[2]))
                    rows.append({"date":dd.isoformat(),"fundingRate":rate,"intervalHours":interval,"raw_row":row})
            except ValueError:
                pass
    rows.sort(key=lambda x:(x["date"],x["raw_row"][0]))
    by_day={d:[] for d in (x.isoformat() for x in days())}
    for r in rows:by_day[r["date"]].append(r)
    missing=[d for d,v in by_day.items() if not v]
    if missing:raise ValueError(f"FUNDING_PAGE_INCOMPLETE:{symbol}:missing={missing[:5]}")
    earliest=min(x["date"] for x in rows);latest=max(x["date"] for x in rows)
    # Page truncation guard: source must reach the full start/end window.
    if earliest!="2026-09-01" or latest!="2026-09-29":raise ValueError(f"FUNDING_PAGE_TRUNCATED:{symbol}:earliest={earliest}:latest={latest}")
    return rows,{"key":f"funding|{symbol}|2026-09","url":url,"sha256":page_sha,"source_kind":"pandabull_binance_history_page","row_count":len(rows),"earliest_date":earliest,"latest_date":latest}

def csvrows(p):
    with zipfile.ZipFile(p) as z:
        names=[n for n in z.namelist() if n.endswith(".csv")]
        if len(names)!=1:raise ValueError("CSV_SHAPE")
        return list(csv.reader(io.StringIO(z.read(names[0]).decode())))

price_files=[];funding_files=[];funding={}
for s in SYMBOLS:
    funding[s],fm=funding_page(s);funding_files.append(fm)
    for dd in days():price_files.append(daily_price(s,dd))

panel=[]
for dd in days():
    ds=dd.isoformat()
    for s in SYMBOLS:
        kr=csvrows(ROOT/"klines"/s/"1d"/f"{s}-1d-{dd:%Y-%m-%d}.zip")
        if len(kr)<2 or len(kr[-1])<5:raise ValueError(f"PRICE_SCHEMA:{s}:{dd}")
        rates=[r["fundingRate"] for r in funding[s] if r["date"]==ds]
        if not rates:raise ValueError(f"FUNDING_GAP:{s}:{dd}")
        panel.append({"date":ds,"symbol":s,"close":float(kr[-1][4]),"funding_obs":len(rates),"funding_mean":sum(rates)/len(rates),"funding_interval_hours":sorted({r["intervalHours"] for r in funding[s] if r["date"]==ds})})

manifest={"dataset_id":"HARMONY-USDM-DAILY-FUNDING-2026-09-ELAPSED","protocol":"HARMONY-ELAPSED-HISTORY-006","start":START.isoformat(),"end":END.isoformat(),"symbols":SYMBOLS,"timeframe":"1d","price_files":sorted(price_files,key=lambda x:x["key"]),"funding_pages":sorted(funding_files,key=lambda x:x["key"])}
mb=json.dumps(manifest,sort_keys=True,indent=2).encode()+b"\n";msha=sha(mb);(OUT/"manifest.json").write_bytes(mb)
pb=json.dumps(panel,sort_keys=True,separators=(",",":")).encode()+b"\n";psha=sha(pb);(OUT/"normalized-panel.json").write_bytes(pb)
status={"protocol":"HARMONY-ELAPSED-HISTORY-006","status":"MATERIALIZED","dataset_id":"HARMONY-USDM-DAILY-FUNDING-2026-09-ELAPSED","start":START.isoformat(),"end":END.isoformat(),"days":29,"symbols":8,"panel_rows":len(panel),"manifest_sha256":msha,"normalized_panel_sha256":psha,"funding_source":"pandabull_binance_history_pages","all_dates_complete":True,"all_symbols_complete":True,"candidate_execution_authorized":False,"oos_claim_authorized":False}
(OUT/"status.json").write_text(json.dumps(status,sort_keys=True,indent=2)+"\n");print(json.dumps(status,sort_keys=True,indent=2))
