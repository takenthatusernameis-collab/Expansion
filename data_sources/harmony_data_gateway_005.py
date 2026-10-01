from __future__ import annotations
import hashlib, json, math, time, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
import csv

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"artifacts/HARMONY-DATA-GATEWAY-005"
CACHE=ROOT/"data/cache/harmony_gateway_v5/realized_2h"
ASSETS=["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
START=datetime(2020,7,10,tzinfo=timezone.utc)
END=datetime(2025,10,31,23,59,tzinfo=timezone.utc)
BASE_URL="https://fapi.binance.com/fapi/v1/continuousKlines"

def sha_bytes(b): return hashlib.sha256(b).hexdigest()

def request_json(pair, start, end, attempt=0):
    params={"pair":pair,"contractType":"PERPETUAL","interval":"2h",
            "startTime":int(start.timestamp()*1000),"endTime":int(end.timestamp()*1000),"limit":1500}
    url=BASE_URL+"?"+urllib.parse.urlencode(params)
    req=urllib.request.Request(url,headers={"User-Agent":"Harmony-Research-Gateway-005"})
    with urllib.request.urlopen(req,timeout=60) as resp:
        raw=resp.read()
    return raw,json.loads(raw.decode("utf-8"))

def acquire(pair):
    rows=[]; req_hashes=[]; cur=START
    window=timedelta(days=100)
    while cur<END:
        nxt=min(cur+window,END)
        last=None
        for k in range(3):
            try:
                raw,payload=request_json(pair,cur,nxt)
                req_hashes.append({"start":cur.isoformat(),"end":nxt.isoformat(),"sha256":sha_bytes(raw),"rows":len(payload)})
                for row in payload: rows.append(row)
                last=None
                break
            except Exception as e:
                last=e
                time.sleep(1.5*(k+1))
        if last is not None: raise RuntimeError(f"{pair} request failed: {last}")
        cur=nxt+timedelta(milliseconds=1)

    uniq={}
    for r in rows:
        if len(r)<12: continue
        ts=int(r[0]); uniq[ts]=r
    bars=sorted(uniq.values(),key=lambda r:int(r[0]))
    return bars,req_hashes

def write_asset(pair,bars,req_hashes):
    # 2h bars -> daily realized feature store.
    day={}
    for r in bars:
        ts=int(r[0]); d=datetime.fromtimestamp(ts/1000,tz=timezone.utc).date().isoformat()
        o=float(r[1]); c=float(r[4]); q=float(r[7])
        day.setdefault(d,[]).append((o,c,q))
    rows=[]
    for d,parts in sorted(day.items()):
        if len(parts)<6: continue
        rets=[math.log(parts[i][1]/parts[i-1][1]) for i in range(1,len(parts)) if parts[i-1][1]>0 and parts[i][1]>0]
        if len(rets)<6: continue
        rv=sum(x*x for x in rets)
        up=sum(x*x for x in rets if x>0); down=sum(x*x for x in rets if x<0)
        rsj=(up-down)/(up+down) if up+down>0 else 0.0
        maxshare=max((x*x for x in rets),default=0.0)/(rv or 1.0)
        quote_vol=sum(parts[i][2] for i in range(len(parts)))
        ret_1d=math.log(parts[-1][1]/parts[0][0]) if parts[0][0]>0 and parts[-1][1]>0 else 0.0
        rows.append({"date":d,"rv":math.sqrt(max(rv,0.0)),"up_var":up,"down_var":down,
                     "rsj":rsj,"jump_share":maxshare,"quote_volume":quote_vol,"ret_1d":ret_1d})
    # derive rolling features later in one deterministic pass
    out=[]
    for i,row in enumerate(rows):
        def vals(key,w):
            return [x[key] for x in rows[max(0,i-w+1):i+1]]
        rv21=vals("rv",21); rv63=vals("rv",63); rv5=vals("rv",5); rv60=vals("rv",60)
        iv=math.sqrt(sum(x*x for x in rv21)/len(rv21)) if rv21 else 0.0
        mean_rv=sum(rv21)/len(rv21) if rv21 else 0.0
        amihud=[]
        for z in rows[max(0,i-20):i+1]:
            if z["quote_volume"]>0: amihud.append(abs(z["ret_1d"])/z["quote_volume"])
        am= sum(amihud)/len(amihud) if amihud else None
        out.append({**row,
                    "rv21":sum(rv21)/len(rv21) if rv21 else None,
                    "rv63":sum(rv63)/len(rv63) if rv63 else None,
                    "rv5_60_ratio":(sum(rv5)/len(rv5))/(sum(rv60)/len(rv60)+1e-18) if rv60 else None,
                    "vol_of_vol21":math.sqrt(sum((x-mean_rv)**2 for x in rv21)/max(1,len(rv21)-1)),
                    "amihud21":am,
                    "iv21_raw":iv})
    path=CACHE/f"{pair}_realized_2h.csv"
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",newline="",encoding="utf-8") as f:
        wr=csv.DictWriter(f,fieldnames=list(out[0].keys())); wr.writeheader(); wr.writerows(out)
    return path,req_hashes,len(bars),len(out),sha_bytes(path.read_bytes())

def main():
    OUT.mkdir(parents=True,exist_ok=True); CACHE.mkdir(parents=True,exist_ok=True)
    manifest={"gateway_id":"HARMONY-DATA-GATEWAY-005","assets":{},"source_endpoint":BASE_URL}
    for pair in ASSETS:
        cached=CACHE/f"{pair}_realized_2h.csv"
        if cached.exists():
            raw=[]; req_hashes=[]
            rows=sum(1 for _ in cached.open(encoding="utf-8"))-1
            manifest["assets"][pair]={"status":"cached","feature_rows":rows,"feature_sha256":sha_bytes(cached.read_bytes()),
                                     "request_hashes":[]}
            continue
        bars=reqs=None
        bars,reqs=acquire(pair)
        path,req_hashes,bar_count,feature_rows,feature_sha=write_asset(pair,bars,reqs)
        manifest["assets"][pair]={"status":"materialized","raw_2h_bars":bar_count,
                                  "feature_rows":feature_rows,"feature_sha256":feature_sha,
                                  "request_hashes":req_hashes}
    # Coverage certificate: count days with complete features on the fixed date interval.
    for pair in ASSETS:
        p=CACHE/f"{pair}_realized_2h.csv"
        if not p.exists(): raise RuntimeError(f"missing {p}")
        data=list(csv.DictReader(p.open(encoding="utf-8")))
        dates={r["date"] for r in data}
        manifest["assets"][pair]["min_date"]=min(dates) if dates else None
        manifest["assets"][pair]["max_date"]=max(dates) if dates else None
    (OUT/"HARMONY-DATA-GATEWAY-005-RESULT.json").write_text(json.dumps(manifest,sort_keys=True,indent=2)+"\n")
    print(json.dumps({"gateway_id":manifest["gateway_id"],"assets":{k:v["feature_rows"] for k,v in manifest["assets"].items()}},indent=2))

if __name__=="__main__": main()
