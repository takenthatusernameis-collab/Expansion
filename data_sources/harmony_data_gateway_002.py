from __future__ import annotations
import hashlib, json
from pathlib import Path
from urllib.parse import quote
import requests

ROOT = Path("data/cache/harmony_gateway_v2")
CM_REPO = "https://raw.githubusercontent.com/coinmetrics/data/f1a36afb962731c387bb03982758ab0103063da5/csv"
VISION = "https://data.binance.vision/data/spot/monthly/klines"
ASSETS = ["btc","eth","ltc","xrp","bnb","bch","ada","doge"]
SYMBOLS = ["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
START="2020-01-01"; END="2025-10-31"
CM_COMMIT="f1a36afb962731c387bb03982758ab0103063da5"

def sha256_path(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()

def download(url: str, out: Path):
    out.parent.mkdir(parents=True,exist_ok=True)
    if out.exists():
        return {"status":"cached","url":url,"path":str(out),"sha256":sha256_path(out),"bytes":out.stat().st_size}
    r=requests.get(url,timeout=120,headers={"User-Agent":"Harmony/data-gateway-002"})
    r.raise_for_status()
    out.write_bytes(r.content)
    return {"status":"downloaded","url":url,"path":str(out),"sha256":sha256_path(out),"bytes":len(r.content)}

def fetch_coinmetrics_csv():
    rows=[]
    for asset in ASSETS:
        url=f"{CM_REPO}/{quote(asset)}.csv"
        out=ROOT/"coinmetrics_public"/f"{asset}.csv"
        rows.append({**download(url,out),"asset":asset})
    return rows

def fetch_spot_month(year,month,symbol):
    name=f"{symbol}-1d-{year:04d}-{month:02d}.zip"
    url=f"{VISION}/{symbol}/1d/{name}"
    out=ROOT/"binance_spot"/symbol/name
    try:
        return {**download(url,out),"symbol":symbol,"year":year,"month":month}
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code==404:
            return {"status":"missing","url":url,"symbol":symbol,"year":year,"month":month}
        raise

def fetch_spot():
    files=[]
    for symbol in SYMBOLS:
        for year in range(2020,2026):
            last_month=10 if year==2025 else 12
            for month in range(1,last_month+1):
                files.append(fetch_spot_month(year,month,symbol))
    return files

def main():
    ROOT.mkdir(parents=True,exist_ok=True)
    cm=fetch_coinmetrics_csv()
    spot=fetch_spot()
    manifest={"gateway_id":"HARMONY-DATA-GATEWAY-002","coinmetrics_source_repo":"coinmetrics/data",
              "coinmetrics_source_commit":CM_COMMIT,"assets":ASSETS,"symbols":SYMBOLS,
              "start":START,"end":END,"coinmetrics_public":cm,
              "binance_spot_file_count":len(spot),"binance_spot_downloaded":sum(x.get("status") in ("downloaded","cached") for x in spot),
              "binance_spot_missing":sum(x.get("status")=="missing" for x in spot)}
    raw=json.dumps(manifest,sort_keys=True,indent=2)+"\n"
    p=ROOT/"gateway_manifest.json"; p.write_text(raw)
    manifest["manifest_sha256"]=hashlib.sha256(raw.encode()).hexdigest()
    (ROOT/"gateway_manifest.json").write_text(json.dumps(manifest,sort_keys=True,indent=2)+"\n")
    print(json.dumps(manifest,sort_keys=True,indent=2))

if __name__=="__main__":
    main()
