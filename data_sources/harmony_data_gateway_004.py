from __future__ import annotations
import hashlib, json
from pathlib import Path
import requests

ROOT=Path("data/cache/harmony_gateway_v4")
VISION="https://data.binance.vision/data/spot/monthly/klines"
SYMBOLS=["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
START_YEAR=2020
START_MONTH=7
END_YEAR=2025
END_MONTH=10

def sha(p: Path)->str:
    return hashlib.sha256(p.read_bytes()).hexdigest()

def month_iter():
    y,m=START_YEAR,START_MONTH
    while (y,m)<=(END_YEAR,END_MONTH):
        yield y,m
        m+=1
        if m==13:
            y+=1; m=1

def fetch(symbol,y,m):
    name=f"{symbol}-1d-{y:04d}-{m:02d}.zip"
    url=f"{VISION}/{symbol}/1d/{name}"
    out=ROOT/"binance_spot"/symbol/name
    out.parent.mkdir(parents=True,exist_ok=True)
    if not out.exists():
        r=requests.get(url,timeout=120,headers={"User-Agent":"Harmony/data-gateway-004"})
        r.raise_for_status()
        out.write_bytes(r.content)
    return {"symbol":symbol,"year":y,"month":m,"url":url,"sha256":sha(out),"bytes":out.stat().st_size}

def main():
    ROOT.mkdir(parents=True,exist_ok=True)
    files=[]
    for s in SYMBOLS:
        for y,m in month_iter():
            files.append(fetch(s,y,m))
    manifest={"gateway_id":"HARMONY-DATA-GATEWAY-004","source":"Binance public historical spot klines",
              "symbols":SYMBOLS,"start":f"{START_YEAR:04d}-{START_MONTH:02d}-01","end":f"{END_YEAR:04d}-{END_MONTH:02d}-31",
              "file_count":len(files),"files":files}
    raw=(json.dumps(manifest,sort_keys=True,indent=2)+"\n").encode()
    manifest["manifest_sha256"]=hashlib.sha256(raw).hexdigest()
    (ROOT/"gateway_manifest.json").write_text(json.dumps(manifest,sort_keys=True,indent=2)+"\n")
    print(json.dumps(manifest,sort_keys=True,indent=2))
if __name__=="__main__": main()
