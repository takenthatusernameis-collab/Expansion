import hashlib,time
from pathlib import Path
from urllib.parse import urljoin
import requests

COINMETRICS="https://community-api.coinmetrics.io/v4"
WIKIMEDIA="https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
BINANCE="https://data.binance.vision/data/futures/um/monthly/klines"
SESSION=requests.Session()
SESSION.headers.update({"User-Agent":"HarmonyResearchDataGateway/1.0"})

def sha256_bytes(data): return hashlib.sha256(data).hexdigest()

def get_json(url,params=None,retries=5):
    last=None
    for attempt in range(retries):
        try:
            r=SESSION.get(url,params=params,timeout=60)
            if r.status_code==429:
                time.sleep(6.5); continue
            r.raise_for_status()
            return r.json(),r.url,r.headers
        except Exception as exc:
            last=exc; time.sleep(min(2**attempt,12))
    raise RuntimeError(f"GET failed: {url}: {last}")

def coinmetrics_paged(path,params,sleep_seconds=0.7):
    url=urljoin(COINMETRICS+"/",path.lstrip("/")); first=True; rows=[]; seen=set()
    while url:
        payload,_,_=get_json(url,params=params if first else None); first=False
        rows.extend(payload.get("data",[])); nxt=payload.get("next_page_url")
        if nxt and nxt in seen: raise RuntimeError("Coin Metrics paging loop")
        if nxt: seen.add(nxt)
        url=nxt
        if url: time.sleep(sleep_seconds)
    return rows

def fetch_asset_metrics(assets,metrics,start_time,end_time,frequency="1d"):
    return coinmetrics_paged("/timeseries/asset-metrics",{"assets":",".join(assets),"metrics":",".join(metrics),"frequency":frequency,"start_time":start_time,"end_time":end_time,"page_size":10000})

def fetch_market_metrics(markets,metrics,start_time,end_time,frequency="1d"):
    return coinmetrics_paged("/timeseries/market-metrics",{"markets":",".join(markets),"metrics":",".join(metrics),"frequency":frequency,"start_time":start_time,"end_time":end_time,"page_size":10000})

def fetch_wikimedia_daily(project,page,start,end):
    url=f"{WIKIMEDIA}/{project}/all-access/user/{page}/daily/{start}/{end}"
    return get_json(url)

def binance_monthly_url(symbol,year,month,interval="1h"):
    return f"{BINANCE}/{symbol}/{interval}/{symbol}-{interval}-{year:04d}-{month:02d}.zip"

def download_binary(url,destination,retries=5):
    destination=Path(destination); destination.parent.mkdir(parents=True,exist_ok=True); last=None
    for attempt in range(retries):
        try:
            r=SESSION.get(url,timeout=90)
            if r.status_code==404: return {"status":"missing","url":url}
            if r.status_code==429:
                time.sleep(6.5); continue
            r.raise_for_status(); data=r.content; destination.write_bytes(data)
            return {"status":"downloaded","url":url,"path":str(destination),"bytes":len(data),"sha256":sha256_bytes(data)}
        except Exception as exc:
            last=exc; time.sleep(min(2**attempt,12))
    raise RuntimeError(f"binary download failed: {url}: {last}")
