from __future__ import annotations
import hashlib, json, time
from pathlib import Path
from urllib.parse import urlencode
import requests

ROOT = Path("data/cache/harmony_gateway_v2")
CM = "https://community-api.coinmetrics.io/v4"
VISION = "https://data.binance.vision/data/spot/monthly/klines"
ASSETS = ["btc","eth","ltc","xrp","bnb","bch","ada","doge"]
SYMBOLS = ["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
START="2020-01-01"; END="2025-10-31"
MARKETS=[f"binance-{s}-future" for s in SYMBOLS]

def sha256_path(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()

def cm_get(path: str, params: dict, timeout=120):
    r=requests.get(CM+path, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()

def fetch_asset_metrics():
    out=[]
    params={
        "assets":",".join(ASSETS),
        "metrics":"AdrNewCnt,CapMrktCurUSD,VolumeTotUSD",
        "frequency":"1d",
        "start_time":START,
        "end_time":END,
        "paging_from":"start",
        "page_size":10000,
    }
    url=CM+"/timeseries/asset-metrics"
    while url:
        r=requests.get(url, params=params if url.endswith("asset-metrics") else None, timeout=120)
        r.raise_for_status()
        payload=r.json()
        out.extend(payload.get("data",[]))
        url=payload.get("next_page_url")
        params=None
        time.sleep(0.15)
    p=ROOT/"coinmetrics_asset_metrics.json"
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(out,sort_keys=True)+"\n")
    return {"rows":len(out),"sha256":sha256_path(p),"path":str(p)}

def fetch_spot_month(year,month,symbol):
    name=f"{symbol}-1d-{year:04d}-{month:02d}.zip"
    url=f"{VISION}/{symbol}/1d/{name}"
    out=ROOT/"binance_spot"/symbol/name
    out.parent.mkdir(parents=True,exist_ok=True)
    if out.exists():
        return {"url":url,"path":str(out),"sha256":sha256_path(out),"bytes":out.stat().st_size}
    r=requests.get(url,timeout=120,headers={"User-Agent":"Harmony/data-gateway-002"})
    r.raise_for_status()
    out.write_bytes(r.content)
    return {"url":url,"path":str(out),"sha256":sha256_path(out),"bytes":len(r.content)}

def fetch_spot():
    files=[]
    for symbol in SYMBOLS:
        for year in range(2020,2026):
            last_month=10 if year==2025 else 12
            for month in range(1,last_month+1):
                try:
                    files.append(fetch_spot_month(year,month,symbol))
                except requests.HTTPError as exc:
                    if exc.response is not None and exc.response.status_code==404:
                        continue
                    raise
    return files

def probe_quotes():
    results={}
    for market in MARKETS:
        params={
            "markets":market,
            "granularity":"1d",
            "start_time":"2024-05-22",
            "end_time":"2024-06-30",
            "paging_from":"start",
            "page_size":1000,
        }
        try:
            payload=cm_get("/timeseries/market-quotes",params)
            rows=payload.get("data",[])
            results[market]={"http_ok":True,"rows":len(rows),
                             "first_time":rows[0].get("time") if rows else None,
                             "last_time":rows[-1].get("time") if rows else None}
        except Exception as exc:
            results[market]={"http_ok":False,"error":type(exc).__name__+":"+str(exc)}
    return results

def main():
    ROOT.mkdir(parents=True,exist_ok=True)
    cm=fetch_asset_metrics()
    spot=fetch_spot()
    quotes=probe_quotes()
    manifest={"gateway_id":"HARMONY-DATA-GATEWAY-002","assets":ASSETS,"symbols":SYMBOLS,
              "start":START,"end":END,"coinmetrics_asset_metrics":cm,
              "binance_spot_file_count":len(spot),"binance_spot":spot,
              "quote_probe":quotes}
    raw=json.dumps(manifest,sort_keys=True,indent=2)+"\n"
    p=ROOT/"gateway_manifest.json"; p.write_text(raw)
    print(json.dumps({**manifest,"manifest_sha256":hashlib.sha256(raw.encode()).hexdigest()},sort_keys=True,indent=2))

if __name__=="__main__":
    main()
