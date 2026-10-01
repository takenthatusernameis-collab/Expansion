from __future__ import annotations
import concurrent.futures, hashlib, json, math, urllib.request, zipfile, io
from datetime import datetime, timezone
from pathlib import Path
import csv

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"artifacts/HARMONY-DATA-GATEWAY-005"
CACHE=ROOT/"data/cache/harmony_gateway_v5/realized_4h"
ASSETS=["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
START=datetime(2020,7,10,tzinfo=timezone.utc)
END=datetime(2025,10,31,23,59,tzinfo=timezone.utc)
BASE_URL="https://fapi.binance.com/fapi/v1/continuousKlines"

def sha_bytes(b): return hashlib.sha256(b).hexdigest()

ARCHIVE_BASE="https://data.binance.vision/data/futures/um/monthly/klines"

def months():
    out=[]; y,m=2020,7
    while (y,m)<=(2025,10):
        out.append((y,m))
        m+=1
        if m==13: y,m=y+1,1
    return out

def download_month(pair, ym):
    y,m=ym
    fn=f"{pair}-4h-{y}-{m:02d}.zip"
    url=f"{ARCHIVE_BASE}/{pair}/4h/{fn}"
    req=urllib.request.Request(url,headers={"User-Agent":"Harmony-Research-Gateway-005"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req,timeout=60) as resp:
                raw=resp.read()
            return ym,raw,hashlib.sha256(raw).hexdigest(),None
        except Exception as e:
            if attempt==2: return ym,None,None,str(e)
    return ym,None,None,"unknown"

def acquire(pair):
    raw_archives=[]; manifest=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futures=[ex.submit(download_month,pair,ym) for ym in months()]
        for fut in futures:
            ym,raw,sha,err=fut.result()
            if err is not None:
                return [],manifest,err
            manifest.append({"year":ym[0],"month":ym[1],"sha256":sha,"bytes":len(raw)})
            raw_archives.append((ym,raw))
    bars=[]
    for ym,raw in raw_archives:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            members=[n for n in z.namelist() if n.lower().endswith(".csv")]
            if len(members)!=1: raise RuntimeError(f"unexpected archive members for {pair} {ym}: {members}")
            rows=csv.reader(z.read(members[0]).decode("utf-8","replace").splitlines())
            for r in rows:
                if r and r[0].isdigit(): bars.append(r)
    return bars,manifest,None


def write_asset(pair,bars,req_manifest):
    day={}
    for r in bars:
        if len(r)<8: continue
        ts=int(r[0]); dt=datetime.fromtimestamp(ts/1000,tz=timezone.utc)
        if dt<datetime(2020,7,10,tzinfo=timezone.utc) or dt>datetime(2025,10,31,23,59,tzinfo=timezone.utc): continue
        d=dt.date().isoformat()
        o=float(r[1]); h=float(r[2]); l=float(r[3]); c=float(r[4]); q=float(r[7])
        day.setdefault(d,[]).append((o,h,l,c,q))
    rows=[]
    for d,parts in sorted(day.items()):
        if len(parts)<4: continue
        rets=[math.log(parts[i][3]/parts[i-1][3]) for i in range(1,len(parts)) if parts[i-1][3]>0 and parts[i][3]>0]
        if len(rets)<4: continue
        rv=sum(x*x for x in rets)
        up=sum(x*x for x in rets if x>0); down=sum(x*x for x in rets if x<0)
        rsj=(up-down)/(up+down) if up+down>0 else 0.0
        maxshare=max((x*x for x in rets),default=0.0)/(rv or 1.0)
        quote_vol=sum(p[4] for p in parts)
        ret_1d=math.log(parts[-1][3]/parts[0][0]) if parts[0][0]>0 and parts[-1][3]>0 else 0.0
        rows.append({"date":d,"rv":math.sqrt(max(rv,0.0)),"up_var":up,"down_var":down,
                     "rsj":rsj,"jump_share":maxshare,"quote_volume":quote_vol,"ret_1d":ret_1d})
    out=[]
    for i,row in enumerate(rows):
        rv21=[x["rv"] for x in rows[max(0,i-20):i+1]]
        rv63=[x["rv"] for x in rows[max(0,i-62):i+1]]
        rv5=[x["rv"] for x in rows[max(0,i-4):i+1]]
        rv60=[x["rv"] for x in rows[max(0,i-59):i+1]]
        mean_rv=sum(rv21)/len(rv21) if rv21 else 0.0
        amihud=[]
        for z in rows[max(0,i-20):i+1]:
            if z["quote_volume"]>0: amihud.append(abs(z["ret_1d"])/z["quote_volume"])
        am=sum(amihud)/len(amihud) if amihud else None
        out.append({**row,
                    "rv21":sum(rv21)/len(rv21) if rv21 else None,
                    "rv63":sum(rv63)/len(rv63) if rv63 else None,
                    "rv5_60_ratio":(sum(rv5)/len(rv5))/(sum(rv60)/len(rv60)+1e-18) if rv60 else None,
                    "vol_of_vol21":math.sqrt(sum((x-mean_rv)**2 for x in rv21)/max(1,len(rv21)-1)),
                    "amihud21":am,"iv21_raw":math.sqrt(sum(x*x for x in rv21)/len(rv21)) if rv21 else None})
    path=CACHE/f"{pair}_realized_4h.csv"; path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",newline="",encoding="utf-8") as f:
        wr=csv.DictWriter(f,fieldnames=list(out[0].keys())); wr.writeheader(); wr.writerows(out)
    return path,len(bars),len(out),sha_bytes(path.read_bytes()),req_manifest


def main():
    OUT.mkdir(parents=True,exist_ok=True); CACHE.mkdir(parents=True,exist_ok=True)
    manifest={"gateway_id":"HARMONY-DATA-GATEWAY-005","assets":{},"source_endpoint":ARCHIVE_BASE}
    for pair in ASSETS:
        path=CACHE/f"{pair}_realized_4h.csv"
        if path.exists():
            rows=sum(1 for _ in path.open(encoding="utf-8"))-1
            manifest["assets"][pair]={"status":"cached","feature_rows":rows,"feature_sha256":sha_bytes(path.read_bytes()),
                                     "request_hashes":[]}
            continue
        bars,reqs,err=acquire(pair)
        if err is not None: raise RuntimeError(f"{pair}: {err}")
        path,bar_count,feature_rows,feature_sha,req_manifest=write_asset(pair,bars,reqs)
        manifest["assets"][pair]={"status":"materialized","raw_4h_bars":bar_count,"feature_rows":feature_rows,
                                  "feature_sha256":feature_sha,"request_hashes":req_manifest}
    for pair in ASSETS:
        p=CACHE/f"{pair}_realized_4h.csv"
        if not p.exists(): raise RuntimeError(f"missing {p}")
        data=list(csv.DictReader(p.open(encoding="utf-8")))
        dates={r["date"] for r in data}
        manifest["assets"][pair]["min_date"]=min(dates) if dates else None
        manifest["assets"][pair]["max_date"]=max(dates) if dates else None
    (OUT/"HARMONY-DATA-GATEWAY-005-RESULT.json").write_text(json.dumps(manifest,sort_keys=True,indent=2)+"\n")
    print(json.dumps({"gateway_id":manifest["gateway_id"],"assets":{k:v["feature_rows"] for k,v in manifest["assets"].items()}},indent=2))


if __name__=="__main__": main()
