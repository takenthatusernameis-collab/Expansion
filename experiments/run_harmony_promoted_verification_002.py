import hashlib, json
from pathlib import Path
from experiments.run_harmony_promoted_validation_001 import run_0012, run_0024

OUT = Path("artifacts/HARMONY-PROMOTED-VERIFICATION-002")
EXPECTED = {
    "HARMONY-FIN-0012": {"cumulative_return": 0.4169009579470373, "sharpe": 0.9671591056994208},
    "HARMONY-FIN-0024": {"cumulative_return": 0.43002307869081013, "sharpe": 1.3589824243083648},
}
def close(a,b,tol=1e-12): return abs(a-b) <= tol
def main():
    runs={}
    for cid,fn in [("HARMONY-FIN-0012",run_0012),("HARMONY-FIN-0024",run_0024)]:
        base=fn(1.0,True)["oos"]
        stress={f"{m:.1f}x":fn(m,True)["oos"] for m in (1.0,2.0,3.0,4.0,5.0)}
        stress["funding_neutral_1.0x"]=fn(1.0,False)["oos"]
        e=EXPECTED[cid]
        runs[cid]={"baseline":base,"stress":stress,"archived_baseline":e,
                   "reproduces_archived_baseline":close(base["cumulative_return"],e["cumulative_return"]) and close(base["sharpe"],e["sharpe"]),
                   "deltas":{"cumulative_return":base["cumulative_return"]-e["cumulative_return"],"sharpe":base["sharpe"]-e["sharpe"]}}
    result={"verification_id":"HARMONY-PROMOTED-VERIFICATION-002","runs":runs,
            "integrity":{"fixed_definitions":True,"parameter_search":False,"universe_search":False,"direction_search":False,"holdout_access":False},
            "all_archived_baselines_reproduced":all(v["reproduces_archived_baseline"] for v in runs.values())}
    OUT.mkdir(parents=True,exist_ok=True)
    raw=(json.dumps(result,sort_keys=True,indent=2)+"\n").encode()
    (OUT/"HARMONY-PROMOTED-VERIFICATION-002-RESULT.json").write_bytes(raw)
    summary={"verification_id":result["verification_id"],"all_archived_baselines_reproduced":result["all_archived_baselines_reproduced"],
             "result_sha256":hashlib.sha256(raw).hexdigest(),"deltas":{k:v["deltas"] for k,v in runs.items()}}
    (OUT/"SUMMARY.json").write_text(json.dumps(summary,sort_keys=True,indent=2)+"\n")
    print(json.dumps(summary,indent=2))
if __name__=="__main__": main()
