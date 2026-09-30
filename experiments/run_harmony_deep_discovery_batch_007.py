import csv
import hashlib
import json
import math
import statistics
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SYMBOLS = ["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
FUT_ROOT = Path("data/cache/binance/futures_um/deep_history_2019")
OUT = Path("artifacts/HARMONY-DEEP-DISCOVERY-BATCH-007")
END = "2025-10-31"
OOS_START = "2024-05-22"
FEE = 0.0006
SLIP = 0.0005

def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()

def parse_zip_rows(path):
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise RuntimeError(f"unexpected archive members: {path}")
        return list(csv.reader(z.open(names[0]).read().decode("utf-8").splitlines()))

def load_panel():
    px={s:{} for s in SYMBOLS}
    funding={s:{} for s in SYMBOLS}
    for s in SYMBOLS:
        for path in sorted((FUT_ROOT/"klines"/s/"1d").glob(f"{s}-1d-*.zip")):
            for row in parse_zip_rows(path):
                if row and row[0].isdigit():
                    d=datetime.fromtimestamp(int(row[0])/1000.0,timezone.utc).date().isoformat()
                    if d<=END:
                        px[s][d]=float(row[4])
        fp=FUT_ROOT/"funding_gateway"/f"{s}-2019-2025-10.json"
        for row in json.loads(fp.read_text()):
            d=datetime.fromtimestamp(int(row["fundingTime"])/1000.0,timezone.utc).date().isoformat()
            if d<=END:
                funding[s].setdefault(d,[]).append(float(row["fundingRate"]))
    dates=sorted(set.intersection(*(set(px[s]) for s in SYMBOLS)))
    if dates[0]!="2020-07-10" or dates[-1]!=END or len(dates)!=1935:
        raise RuntimeError(f"unexpected common panel {dates[:1]}..{dates[-1:]} n={len(dates)}")
    return dates,px,funding

def realized_vol(values):
    if len(values)<2:
        raise ValueError("need at least two returns")
    return statistics.pstdev(values)

def month_key(d):
    return d[:7]

def month_ends(dates):
    out={}
    for d in dates:
        out[month_key(d)]=d
    return out

def build_monthly_targets(dates,px):
    ends=month_ends(dates)
    months=sorted(ends)
    targets={}
    for idx,m in enumerate(months):
        if idx==0:
            continue
        prev_end=ends[months[idx-1]]
        formation_idx=dates.index(prev_end)
        if formation_idx<63:
            continue
        vols=[]
        for s in SYMBOLS:
            rs=[]
            for j in range(formation_idx-62,formation_idx+1):
                if j==0:
                    continue
                d=dates[j]; pd=dates[j-1]
                rs.append(px[s][d]/px[s][pd]-1.0)
            if len(rs)!=63:
                raise RuntimeError("formation return count mismatch")
            vols.append((realized_vol(rs),s))
        vols.sort(key=lambda z:(z[0],z[1]))
        w={s:0.0 for s in SYMBOLS}
        for _,s in vols[:3]:
            w[s]=1.0/6.0
        for _,s in vols[-3:]:
            w[s]=-1.0/6.0
        targets[m]=w
    return targets

def simulate(dates,px,funding,targets,cost_mult=1.0):
    eq=1.0
    prev={s:0.0 for s in SYMBOLS}
    curve=[]
    turnover=0.0
    funding_sum=0.0
    last_month=None
    for i,d in enumerate(dates):
        m=month_key(d)
        for s in SYMBOLS:
            for rate in funding[s].get(d,[]):
                pnl=-prev[s]*rate
                eq*=1.0+pnl
                funding_sum+=pnl
        if i>0:
            pd=dates[i-1]
            eq*=1.0+sum(prev[s]*(px[s][d]/px[s][pd]-1.0) for s in SYMBOLS)
        if m!=last_month:
            target=targets.get(m)
            if target is not None:
                delta=sum(abs(target[s]-prev[s]) for s in SYMBOLS)
                eq*=max(0.0,1.0-(FEE+SLIP)*cost_mult*delta)
                turnover+=delta/2.0
                prev=target.copy()
            last_month=m
        curve.append(eq)
    liq=sum(abs(v) for v in prev.values())
    eq*=max(0.0,1.0-(FEE+SLIP)*cost_mult*liq)
    curve[-1]=eq
    return {"equity":curve,"turnover":turnover,"funding_pnl_sum":funding_sum}

def metrics(curve):
    rr=[curve[i]/curve[i-1]-1.0 for i in range(1,len(curve))]
    sd=statistics.stdev(rr) if len(rr)>1 else 0.0
    sharpe=statistics.mean(rr)/sd*math.sqrt(365.25) if sd else 0.0
    peak=curve[0]; mdd=0.0
    for x in curve:
        peak=max(peak,x); mdd=min(mdd,x/peak-1.0)
    years=max((len(curve)-1)/365.25,1e-12)
    return {"final_equity":curve[-1],"cumulative_return":curve[-1]-1.0,"cagr":curve[-1]**(1.0/years)-1.0,"sharpe":sharpe,"max_drawdown":mdd,"observations":len(curve)}

def segment(dates,equity,start):
    idx=[i for i,d in enumerate(dates) if d>=start]
    base=equity[idx[0]-1] if idx[0]>0 else 1.0
    c=[1.0]+[equity[i]/base for i in idx]
    out=metrics(c); out.update({"start":dates[idx[0]],"end":dates[idx[-1]]})
    return out

def halves(dates,equity,start):
    idx=[i for i,d in enumerate(dates) if d>=start]; mid=len(idx)//2
    def part(xs):
        base=equity[xs[0]-1] if xs[0]>0 else 1.0
        c=[1.0]+[equity[i]/base for i in xs]
        out=metrics(c); out.update({"start":dates[xs[0]],"end":dates[xs[-1]]})
        return out
    return {"first_half":part(idx[:mid]),"second_half":part(idx[mid:])}

def equal_targets(months):
    return {m:{s:1.0/len(SYMBOLS) for s in SYMBOLS} for m in months}

def btc_targets(months):
    return {m:{"BTCUSDT":1.0} for m in months}

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    dates,px,funding=load_panel()
    targets=build_monthly_targets(dates,px)
    months=sorted(targets)
    results={}
    for m in (1.0,1.5,2.0):
        run=simulate(dates,px,funding,targets,m)
        results[f"{m:.1f}x"]={
            "metrics":metrics(run["equity"]),
            "oos":segment(dates,run["equity"],OOS_START),
            "oos_halves":halves(dates,run["equity"],OOS_START),
            "turnover":run["turnover"],
            "funding_pnl_sum":run["funding_pnl_sum"],
        }
    eq=simulate(dates,px,funding,equal_targets(months),1.0)
    btc=simulate(dates,px,funding,btc_targets(months),1.0)
    manifest={
        "batch_id":"HARMONY-DEEP-DISCOVERY-BATCH-007",
        "futures_cache_key":"harmony-binance-um-deep-history-2019-2025-10-v1-36777989764",
        "panel":{"start":dates[0],"end":dates[-1],"rows":len(dates)},
        "candidate":"HARMONY-FIN-0036",
        "formation_days":63,
        "formation_cutoff":"prior_common_month_end",
        "rebalance":"first_common_day_of_month",
    }
    mraw=(json.dumps(manifest,sort_keys=True,indent=2)+"\n").encode()
    msha=sha256_bytes(mraw)
    (OUT/"input-manifest.json").write_bytes(mraw)
    payload={
      "batch_id":"HARMONY-DEEP-DISCOVERY-BATCH-007",
      "input_manifest_sha256":msha,
      "candidate":{"HARMONY-FIN-0036":{
        "base":results["1.0x"],
        "cost_stress":{k:v for k,v in results.items()},
        "signal_month_count":len(months)
      }},
      "benchmarks":{
        "BTCUSDT_buy_and_hold":{"metrics":metrics(btc["equity"]),"oos":segment(dates,btc["equity"],OOS_START),"oos_halves":halves(dates,btc["equity"],OOS_START)},
        "same_universe_equal_weight_long_only":{"metrics":metrics(eq["equity"]),"oos":segment(dates,eq["equity"],OOS_START),"oos_halves":halves(dates,eq["equity"],OOS_START)}
      },
      "integrity":{"holdout_access":False,"parameter_search":False,"direction_search":False,"universe_search":False,"candidate_mutation":False}
    }
    raw=(json.dumps(payload,sort_keys=True,indent=2)+"\n").encode()
    rsha=sha256_bytes(raw)
    (OUT/"HARMONY-DEEP-DISCOVERY-BATCH-007-RESULT.json").write_bytes(raw)
    (OUT/"HARMONY-DEEP-DISCOVERY-BATCH-007-SUMMARY.json").write_text(json.dumps({"batch_id":payload["batch_id"],"result_sha256":rsha,"candidate":payload["candidate"]},sort_keys=True,indent=2)+"\n")
    print(json.dumps({"batch_id":payload["batch_id"],"result_sha256":rsha},indent=2))

if __name__=="__main__":
    main()
