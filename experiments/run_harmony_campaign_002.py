import csv,hashlib,json,statistics,zipfile,io
from datetime import datetime,timezone
from pathlib import Path

S=["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
ROOT=Path("data/cache/binance/futures_um/monthly")
FEATURE=Path("data/cache/features/funding_carry_v1/features.jsonl.gz")
OUT=Path("artifacts/HARMONY-CAMPAIGN-002")
OUT.mkdir(parents=True,exist_ok=True)
FEE=.0006; SLIP=.0005; REBALANCE=7
DATES=[]; px={s:{} for s in S}; fund={s:{} for s in S}

def day(ms): return datetime.fromtimestamp(ms/1000,timezone.utc).date().isoformat()
def months():
    out=[]; y,m=2021,1
    while (y,m)<=(2025,10):
        out.append((y,m)); m+=1
        if m==13:y,m=y+1,1
    return out
def rows(path):
    with zipfile.ZipFile(path) as z:
        n=[x for x in z.namelist() if x.lower().endswith(".csv")]
        if len(n)!=1: raise RuntimeError(path)
        return list(csv.reader(io.StringIO(z.read(n[0]).decode())))
for s in S:
    for y,m in months():
        p=ROOT/"klines"/s/"1d"/f"{s}-1d-{y:04d}-{m:02d}.zip"
        for r in rows(p):
            if r and r[0].isdigit(): px[s][day(int(r[0]))]=float(r[4])
DATES=sorted(set.intersection(*(set(px[s]) for s in S)))
assert len(DATES)==1760

import gzip
with gzip.open(FEATURE,"rt",encoding="utf-8") as h:
    feature_rows=[json.loads(line) for line in h if line.strip()]
feat={(r["symbol"],r["date"]):r["values"] for r in feature_rows}
assert len(feature_rows)==14080

CAND=[
("funding_carry_mean_1d_weekly_v1","mean_1d"),
("funding_carry_mean_3d_weekly_v1","mean_3d"),
("funding_carry_mean_7d_weekly_v1","mean_7d"),
("funding_carry_zscore_7d_weekly_v1","z"),
]
for s in S:
    for y,m in months():
        p=ROOT/"fundingRate"/s/f"{s}-fundingRate-{y:04d}-{m:02d}.zip"
        rr=rows(p); h={k.strip():i for i,k in enumerate(rr[0])}
        for r in rr[1:]:
            if r: fund[s].setdefault(day(int(r[h["calc_time"]])),[]).append(float(r[h["last_funding_rate"]]))

def target(cid,d):
    vals=[]
    for s in S:
        v=feat[(s,d)]
        if cid.endswith("zscore_7d_weekly_v1"):
            score=-(v["mean_7d"]/v["std_30d"]) if v["mean_7d"] is not None and v["std_30d"] not in (None,0) else None
        else:
            key=next(k for c,k in CAND if c==cid)
            score=-v[key] if v[key] is not None else None
        if score is None:return None
        vals.append((score,s))
    vals.sort(key=lambda z:(-z[0],z[1]))
    w={s:0.0 for s in S}
    for _,s in vals[:2]:w[s]=.25
    for _,s in vals[-2:]:w[s]=-.25
    return w

def run(cid,a,b,cost_mult=1.0):
    eq=1.; prev={s:0. for s in S}; curve=[]; turnover=0.; cost_sum=0.
    for i in range(a,b):
        d=DATES[i]
        for s in S:
            for rate in fund[s].get(d,[]): eq*=1-prev[s]*rate
        if i>a:
            pd=DATES[i-1]; eq*=1+sum(prev[s]*(px[s][d]/px[s][pd]-1) for s in S)
        if (i-a)%REBALANCE==0:
            tgt=target(cid,d) or prev.copy()
        else:tgt=prev.copy()
        delta=sum(abs(tgt[s]-prev[s]) for s in S)
        turnover += delta/2
        cost=(FEE+SLIP)*cost_mult*delta
        eq*=1-cost; cost_sum+=cost; prev=tgt; curve.append(eq)
    liq=sum(abs(v) for v in prev.values())
    eq*=1-(FEE+SLIP)*cost_mult*liq; curve[-1]=eq
    rr=[curve[i]/curve[i-1]-1 for i in range(1,len(curve))]
    sd=statistics.stdev(rr) if len(rr)>1 else 0
    sh=(statistics.mean(rr)/sd)*(365.25**.5) if sd else 0
    peak=curve[0]; mdd=0
    for x in curve: peak=max(peak,x); mdd=min(mdd,x/peak-1)
    return {"cumulative_return":curve[-1]-1,"sharpe":sh,"max_drawdown":mdd,"turnover":turnover}

edges=[round(i*len(DATES)/6) for i in range(7)]
summary={"campaign_id":"HARMONY-CAMPAIGN-002","parent_campaign":"HARMONY-CAMPAIGN-001","input_manifest_sha256":"c5c3807d4a244b7c1b788fcc1bf84f444301794ee555af8f3f5d56de77b41f73","feature_store_sha256":"c28a40c4a4e4603852bb09ef5aa9fc5723688f6c30f219fe0d9e2fae4802ed5e","blocks":{}}
for cid,_ in CAND:
    blocks=[]
    for j in range(6):
        a,b=edges[j],edges[j+1]
        blocks.append({"block":j+1,"start":DATES[a],"end":DATES[b-1],"observations":b-a,"base":run(cid,a,b,1.0),"cost_stress":{"1.0x":run(cid,a,b,1.0),"1.5x":run(cid,a,b,1.5),"2.0x":run(cid,a,b,2.0)}})
    raw=json.dumps({"candidate_id":cid,"blocks":blocks},sort_keys=True,indent=2).encode()+b"\n"
    sha=hashlib.sha256(raw).hexdigest()
    (OUT/f"{cid}.json").write_bytes(raw)
    summary["blocks"][cid]={"result_sha256":sha,"blocks":blocks}
(OUT/"summary.json").write_text(json.dumps(summary,sort_keys=True,indent=2)+"\n")
print(json.dumps({"campaign_id":"HARMONY-CAMPAIGN-002","candidate_count":4,"block_count":6},sort_keys=True))
