from __future__ import annotations
import csv, hashlib, json, math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RAW=ROOT/"data/cache/harmony_gateway_v7/raw"
FEATURES=ROOT/"data/cache/harmony_gateway_v7/features"
OUT=ROOT/"artifacts/HARMONY-DATA-GATEWAY-007"
SNAPSHOT=RAW/"LMNUF12M_2019-12_2025-10.csv"

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()

def load_snapshot():
    rows=[]
    with SNAPSHOT.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append((r["date"],float(r["value"])))
    assert rows[0][0]=="2019-12" and rows[-1][0]=="2025-10"
    return rows

def main():
    OUT.mkdir(parents=True,exist_ok=True);RAW.mkdir(parents=True,exist_ok=True);FEATURES.mkdir(parents=True,exist_ok=True)
    if not SNAPSHOT.exists():
        raise RuntimeError("Frozen FRED snapshot file missing")
    raw_sha=sha(SNAPSHOT)
    rows=load_snapshot()
    out=[]
    prev=None
    for m,v in rows:
        shock=None if prev is None else math.log(v/prev)
        out.append((m,v,shock));prev=v
    p=FEATURES/"external_uncertainty_monthly.csv"
    with p.open("w",newline="",encoding="utf-8") as fh:
        w=csv.writer(fh);w.writerow(["month","finu_level","finu_log_change"])
        for r in out:w.writerow(r)
    result={"gateway_id":"HARMONY-DATA-GATEWAY-007","source_series":"LMNUF12M",
            "snapshot_sha256":raw_sha,"normalized_sha256":sha(p),
            "rows":len(rows),"start":rows[0][0],"end":rows[-1][0],
            "vintage_proof":False,"guarded_promotion_blocked":True,
            "source_note":"Values frozen from FRED table output observed 2026-10-01; historical vintage provenance is not established."}
    (OUT/"HARMONY-DATA-GATEWAY-007-RESULT.json").write_text(json.dumps(result,sort_keys=True,indent=2)+"\n")
    print(json.dumps(result,indent=2))

if __name__=="__main__":main()
