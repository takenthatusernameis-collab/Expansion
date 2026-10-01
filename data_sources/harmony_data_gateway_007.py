from __future__ import annotations
import csv, hashlib, json, math, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RAW=ROOT/"data/cache/harmony_gateway_v7/raw"
OUT=ROOT/"artifacts/HARMONY-DATA-GATEWAY-007"
FINU=RAW/"LMNUF12M.csv"
EPU=RAW/"USEPUINDXD.csv"
FINU_URL="https://fred.stlouisfed.org/graph/fredgraph.csv?id=LMNUF12M"
EPU_URL="https://fred.stlouisfed.org/graph/fredgraph.csv?id=USEPUINDXD"
ASSETS=["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]

def sha(p: Path): return hashlib.sha256(p.read_bytes()).hexdigest()

def get(url,path):
    req=urllib.request.Request(url,headers={"User-Agent":"Harmony-Research-Gateway-007"})
    with urllib.request.urlopen(req,timeout=60) as r: b=r.read()
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b)
    return sha(path),len(b)

def load_fred(p,series):
    rows=[]
    with p.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("DATE") in (None,"."): continue
            v=r.get(series)
            if v in (None,".",""): continue
            rows.append((r["DATE"],float(v)))
    return rows

def monthly_finu():
    rows=load_fred(FINU,"LMNUF12M")
    by=[]
    prev=None
    for d,v in rows:
        dt=datetime.fromisoformat(d).replace(tzinfo=timezone.utc)
        if prev is not None and prev>0 and v>0:
            by.append((dt.strftime("%Y-%m"),math.log(v/prev)))
        prev=v
    return by

def monthly_epu():
    rows=load_fred(EPU,"USEPUINDXD")
    acc={}
    for d,v in rows:
        m=d[:7];acc.setdefault(m,[]).append(v)
    return [(m,sum(v)/len(v)) for m,v in sorted(acc.items())]

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    finu_sha,finu_bytes=get(FINU_URL,FINU) if not FINU.exists() else (sha(FINU),FINU.stat().st_size)
    epu_sha,epu_bytes=get(EPU_URL,EPU) if not EPU.exists() else (sha(EPU),EPU.stat().st_size)
    finu=monthly_finu();epu=monthly_epu()
    (ROOT/"data/cache/harmony_gateway_v7/features").mkdir(parents=True,exist_ok=True)
    path=ROOT/"data/cache/harmony_gateway_v7/features/external_uncertainty_monthly.csv"
    with path.open("w",newline="",encoding="utf-8") as f:
        w=csv.writer(f);w.writerow(["month","finu_log_change","epu_monthly_mean"])
        em=dict(epu)
        for m,x in finu:w.writerow([m,x,em.get(m)])
    result={
      "gateway_id":"HARMONY-DATA-GATEWAY-007",
      "finu_source":{"url":FINU_URL,"sha256":finu_sha,"bytes":finu_bytes},
      "epu_source":{"url":EPU_URL,"sha256":epu_sha,"bytes":epu_bytes},
      "normalized_sha256":sha(path),
      "financial_uncertainty_months":len(finu),
      "policy_uncertainty_months":len(epu),
      "vintage_proof":False,
      "promotion_restriction":"No guarded promotion from this gateway until vintage/PIT evidence exists."
    }
    (OUT/"HARMONY-DATA-GATEWAY-007-RESULT.json").write_text(json.dumps(result,sort_keys=True,indent=2)+"\n")
    print(json.dumps(result,indent=2))

if __name__=="__main__":
    main()
