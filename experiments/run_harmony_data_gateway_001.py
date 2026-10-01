import json
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
from data_sources.harmony_data_gateway import binance_monthly_url,download_binary,fetch_asset_metrics,fetch_market_metrics,fetch_wikimedia_daily

OUT=Path("artifacts/HARMONY-DATA-GATEWAY-001"); RAW=Path("data/cache/harmony_gateway_001")
ASSETS=["btc","eth","ltc","xrp","bnb","bch","ada","doge"]
SYMBOLS=["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
PAGES=["Bitcoin","Ethereum","Litecoin","XRP","BNB","Bitcoin_Cash","Cardano","Dogecoin"]
CM_START="2020-01-01"; CM_END="2025-10-31"
WIKI_RANGES=[("2020-01-01","2020-12-31"),("2021-01-01","2021-12-31"),("2022-01-01","2022-12-31"),("2023-01-01","2023-12-31"),("2024-01-01","2024-12-31"),("2025-01-01","2025-10-31")]

def save(p,x):
    p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(x,sort_keys=True)); return str(p)

def main():
    OUT.mkdir(parents=True,exist_ok=True); RAW.mkdir(parents=True,exist_ok=True)
    result={"gateway_id":"HARMONY-DATA-GATEWAY-001","fixed_assets":ASSETS,"fixed_symbols":SYMBOLS,"coinmetrics":{},"wikimedia":{},"binance_intraday":{},
            "bottleneck_contracts":{"point_in_time":"Only observations timestamped by signal formation are admissible.","coverage":"Missing observations are reported, never current-value backfilled.","provenance":"Raw API payloads and binary files are retained and hashed.","universe":"The gateway never changes the fixed eight-asset universe to improve coverage."}}
    try:
        cm=fetch_asset_metrics(ASSETS,["AdrActCnt","CapMrktCurUSD","CapMVRVCur"],CM_START,CM_END); save(RAW/"coinmetrics_asset_metrics.json",cm)
        result["coinmetrics"]["asset_metrics"]={"status":"downloaded","rows":len(cm),"metrics":["AdrActCnt","CapMrktCurUSD","CapMVRVCur"]}
    except Exception as exc: result["coinmetrics"]["asset_metrics"]={"status":"blocked","error":str(exc)}
    markets=[f"binance-{s}-future" for s in SYMBOLS]
    try:
        liq=fetch_market_metrics(markets,["liquidations_reported_future_buy_units_1d","liquidations_reported_future_sell_units_1d"],CM_START,CM_END); save(RAW/"coinmetrics_liquidations.json",liq)
        result["coinmetrics"]["liquidations"]={"status":"downloaded","rows":len(liq)}
    except Exception as exc: result["coinmetrics"]["liquidations"]={"status":"blocked","error":str(exc)}
    for page in PAGES:
        result["wikimedia"][page]=[]
        for start,end in WIKI_RANGES:
            try:
                payload,url,_=fetch_wikimedia_daily("en.wikipedia.org",page,start,end); save(RAW/"wikimedia"/f"{page}-{start}-{end}.json",payload)
                result["wikimedia"][page].append({"start":start,"end":end,"status":"downloaded","rows":len(payload.get("items",[])),"url":url})
            except Exception as exc:
                result["wikimedia"][page].append({"start":start,"end":end,"status":"blocked","error":str(exc)})
    jobs=[(s,y,m) for s in SYMBOLS for y in range(2020,2026) for m in range(1,13) if (y,m)<=(2025,10)]
    def one(job):
        s,y,m=job; u=binance_monthly_url(s,y,m,"1h"); d=RAW/"binance_1h"/s/f"{s}-1h-{y:04d}-{m:02d}.zip"; return job,download_binary(u,d)
    with ThreadPoolExecutor(max_workers=8) as pool:
        for fut in as_completed([pool.submit(one,j) for j in jobs]):
            (s,y,m),rec=fut.result(); result["binance_intraday"][f"{s}-{y:04d}-{m:02d}"]=rec
    result["readiness"]={"coinmetrics_asset_metrics_ready":result["coinmetrics"].get("asset_metrics",{}).get("status")=="downloaded",
      "coinmetrics_liquidations_ready":result["coinmetrics"].get("liquidations",{}).get("status")=="downloaded",
      "wikimedia_ready":all(x.get("status")=="downloaded" for v in result["wikimedia"].values() for x in v),
      "binance_intraday_ready":all(x.get("status")=="downloaded" for x in result["binance_intraday"].values())}
    save(OUT/"HARMONY-DATA-GATEWAY-001-RESULT.json",result); print(json.dumps(result["readiness"],indent=2))
if __name__=="__main__": main()
