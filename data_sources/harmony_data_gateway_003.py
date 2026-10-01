from pathlib import Path
import hashlib, json, requests

ROOT=Path("data/cache/harmony_gateway_v3")
CM_BASE="https://raw.githubusercontent.com/coinmetrics/data/f1a36afb962731c387bb03982758ab0103063da5/csv"
CM_COMMIT="f1a36afb962731c387bb03982758ab0103063da5"
ASSETS=["btc","eth","ltc","xrp","bnb","bch","ada","doge"]

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    ROOT.mkdir(parents=True,exist_ok=True); rows=[]
    for a in ASSETS:
        url=f"{CM_BASE}/{a}.csv"; out=ROOT/"coinmetrics_public"/f"{a}.csv"; out.parent.mkdir(parents=True,exist_ok=True)
        if not out.exists():
            r=requests.get(url,timeout=120,headers={"User-Agent":"Harmony/data-gateway-003"})
            r.raise_for_status(); out.write_bytes(r.content)
        rows.append({"asset":a,"url":url,"sha256":sha(out),"bytes":out.stat().st_size})
    manifest={"gateway_id":"HARMONY-DATA-GATEWAY-003","source":"coinmetrics/data","source_commit":CM_COMMIT,"assets":ASSETS,"files":rows}
    raw=(json.dumps(manifest,sort_keys=True,indent=2)+"\n").encode()
    manifest["manifest_sha256"]=hashlib.sha256(raw).hexdigest()
    (ROOT/"gateway_manifest.json").write_text(json.dumps(manifest,sort_keys=True,indent=2)+"\n")
    print(json.dumps(manifest,sort_keys=True,indent=2))
if __name__=="__main__": main()
