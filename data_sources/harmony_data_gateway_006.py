from __future__ import annotations
import concurrent.futures, csv, hashlib, io, json, math, urllib.request, zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"artifacts/HARMONY-DATA-GATEWAY-006"
CACHE=ROOT/"data/cache/harmony_gateway_v6/flow_4h"
ASSETS=["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
BASE="https://data.binance.vision/data/futures/um/monthly/klines"

def sha(b): return hashlib.sha256(b).hexdigest()

def months():
    out=[]; y,m=2020,7
    while (y,m)<=(2025,10):
        out.append((y,m)); m+=1
        if m==13:y,m=y+1,1
    return out

def get_month(pair,ym):
    y,m=ym; fn=f"{pair}-4h-{y}-{m:02d}.zip"; url=f"{BASE}/{pair}/4h/{fn}"
    req=urllib.request.Request(url,headers={"User-Agent":"Harmony-Research-Gateway-006"})
    last=None
    for _ in range(3):
        try:
            with urllib.request.urlopen(req,timeout=60) as resp: raw=resp.read()
            return ym,raw,sha(raw),None
        except Exception as e: last=e
    return ym,None,None,str(last)

def acquire(pair):
    jobs=[(pair,m) for m in months()];out=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for ym,raw,digest,err in ex.map(lambda x:get_month(*x),jobs):
            if err: raise RuntimeError(f"{pair} {ym}: {err}")
            out.append((ym,raw,digest))
    bars=[];manifest=[]
    for ym,raw,digest in out:
        manifest.append({"year":ym[0],"month":ym[1],"sha256":digest,"bytes":len(raw)})
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            ns=[n for n in z.namelist() if n.lower().endswith(".csv")]
            if len(ns)!=1: raise RuntimeError(f"unexpected members {pair} {ym}")
            for r in csv.reader(z.read(ns[0]).decode("utf-8","replace").splitlines()):
                if r and r[0].isdigit(): bars.append(r)
    return bars,manifest

def materialize(pair,bars,manifest):
    days={}
    for r in bars:
        if len(r)<11: continue
        ts=int(r[0]);dt=datetime.fromtimestamp(ts/1000,tz=timezone.utc)
        if not (datetime(2020,7,10,tzinfo=timezone.utc)<=dt<=datetime(2025,10,31,23,59,tzinfo=timezone.utc)):continue
        d=dt.date().isoformat()
        days.setdefault(d,[]).append((float(r[4]),float(r[5]),float(r[7]),float(r[9]),float(r[10])))
    rows=[]
    for d,parts in sorted(days.items()):
        if len(parts)<4:continue
        rets=[math.log(parts[i][0]/parts[i-1][0]) for i in range(1,len(parts)) if parts[i-1][0]>0 and parts[i][0]>0]
        if not rets:continue
        rv=math.sqrt(sum(x*x for x in rets));vol=sum(p[1] for p in parts);qv=sum(p[2] for p in parts)
        tb=sum(p[3] for p in parts);tq=sum(p[4] for p in parts)
        imb=2*tb/vol-1 if vol>0 else 0.;qimb=2*tq/qv-1 if qv>0 else 0.
        rows.append({"date":d,"flow_imbalance_1d":imb,"quote_flow_imbalance_1d":qimb,"quote_volume_1d":qv,"ret_1d":sum(rets),"rv_1d":rv})
    out=[]
    for i,row in enumerate(rows):
        f21=[x["flow_imbalance_1d"] for x in rows[max(0,i-20):i+1]]
        f63=[x["flow_imbalance_1d"] for x in rows[max(0,i-62):i+1]]
        rv21=[x["rv_1d"] for x in rows[max(0,i-20):i+1]]
        mean=sum(f21)/len(f21);sd=math.sqrt(sum((x-mean)**2 for x in f21)/max(1,len(f21)-1))
        flow21=mean;flow63=sum(f63)/len(f63)
        out.append({**row,"flow_imbalance_21d":flow21,"flow_imbalance_63d":flow63,
                    "flow_z_21d":(row["flow_imbalance_1d"]-mean)/(sd+1e-12),
                    "flow_acceleration":flow21-flow63,
                    "flow_return_product":row["flow_imbalance_1d"]*row["ret_1d"],
                    "flow_abs_vol_interaction":row["flow_imbalance_1d"]*(sum(rv21)/len(rv21))})
    path=CACHE/f"{pair}_flow_4h.csv";path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",newline="",encoding="utf-8") as fh:
        wr=csv.DictWriter(fh,fieldnames=list(out[0].keys()));wr.writeheader();wr.writerows(out)
    return path,len(bars),len(out),sha(path.read_bytes()),manifest

def main():
    OUT.mkdir(parents=True,exist_ok=True);CACHE.mkdir(parents=True,exist_ok=True)
    manifest={"gateway_id":"HARMONY-DATA-GATEWAY-006","assets":{}}
    for pair in ASSETS:
        path=CACHE/f"{pair}_flow_4h.csv"
        if path.exists():
            rows=sum(1 for _ in path.open(encoding="utf-8"))-1
            manifest["assets"][pair]={"status":"cached","feature_rows":rows,"feature_sha256":sha(path.read_bytes()),"request_hashes":[]};continue
        bars,reqs=acquire(pair);path,nbar,nfeat,fsha,req_manifest=materialize(pair,bars,reqs)
        manifest["assets"][pair]={"status":"materialized","raw_4h_bars":nbar,"feature_rows":nfeat,"feature_sha256":fsha,"request_hashes":req_manifest}
    (OUT/"HARMONY-DATA-GATEWAY-006-RESULT.json").write_text(json.dumps(manifest,sort_keys=True,indent=2)+"\n")
    print(json.dumps({"gateway_id":manifest["gateway_id"],"assets":{a:v["feature_rows"] for a,v in manifest["assets"].items()}},indent=2))
if __name__=="__main__":main()
