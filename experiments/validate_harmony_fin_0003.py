#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import statistics
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SYMBOLS = ["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
CACHE = Path("data/cache/binance/spot/monthly/klines_1d")
START, END = "2021-01-01", "2025-10-31"
OOS_EXPECTED_START, OOS_EXPECTED_END = "2024-05-20", "2025-10-31"
LOOKBACK = 20
BASE_COST = 0.0006 + 0.0005
TARGET_EQUITY = 3.1838157773259965
TOL = 1e-12


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def date_from_open_ms(raw: int) -> str:
    seconds = raw / (1_000_000 if raw >= 10**14 else 1_000)
    return datetime.fromtimestamp(seconds, timezone.utc).date().isoformat()


def expected_months():
    out=[]
    y,m=2021,1
    while (y,m)<=(2025,10):
        out.append((y,m))
        m += 1
        if m == 13:
            y,m=y+1,1
    return out


def load_local_cache():
    books={s:{} for s in SYMBOLS}
    cache_hashes=[]
    for symbol in SYMBOLS:
        for y,m in expected_months():
            name=f"{symbol}-1d-{y:04d}-{m:02d}.zip"
            path=CACHE/symbol/"1d"/name
            if not path.exists():
                raise FileNotFoundError(path)
            raw=path.read_bytes()
            cache_hashes.append((str(path),digest_bytes(raw)))
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                members=[n for n in z.namelist() if n.lower().endswith(".csv")]
                if len(members)!=1:
                    raise AssertionError(f"{name}: expected one csv")
                rows=csv.reader(io.StringIO(z.read(members[0]).decode("utf-8")))
                for row in rows:
                    if not row or not row[0].strip().isdigit():
                        continue
                    if len(row)<6:
                        raise AssertionError(f"{name}: malformed row")
                    books[symbol][date_from_open_ms(int(row[0]))]=float(row[4])
    common=None
    for s in SYMBOLS:
        ds=set(books[s])
        common=ds if common is None else common & ds
    dates=sorted(common)
    if dates[0]!=START or dates[-1]!=END or len(dates)!=1765:
        raise AssertionError("cached panel identity mismatch")
    return dates,books,cache_hashes


def target_weights(dates,books,i,universe):
    if i < LOOKBACK + 1:
        return {s:0.0 for s in universe}
    prior=dates[i-1]
    anchor=dates[i-LOOKBACK-1]
    ranking=[]
    for s in universe:
        score=books[s][prior]/books[s][anchor]-1.0
        ranking.append((score,s))
    ranking.sort(key=lambda x:(-x[0],x[1]))
    w={s:0.0 for s in universe}
    for s in [s for _,s in ranking[:2]]:
        w[s]=0.5
    return w


def run(dates,books,start_idx,universe,cost_rate):
    eq=1.0
    prev={s:0.0 for s in universe}
    curve=[]
    for i in range(start_idx,len(dates)):
        if i==start_idx:
            gross=0.0
        else:
            d=dates[i]
            p=dates[i-1]
            gross=sum(prev[s]*(books[s][d]/books[s][p]-1.0) for s in universe)
        eq_before=eq*(1+gross)
        target=target_weights(dates,books,i,universe)
        delta=sum(abs(target[s]-prev[s]) for s in universe)
        eq=eq_before*(1-cost_rate*delta)
        curve.append(eq)
        prev=target
    liquidation=sum(abs(v) for v in prev.values())
    eq*=1-cost_rate*liquidation
    curve[-1]=eq
    returns=[curve[i]/curve[i-1]-1 for i in range(1,len(curve))]
    sd=statistics.stdev(returns) if len(returns)>=2 else 0.0
    sharpe=(statistics.mean(returns)/sd)*math.sqrt(365.0) if sd else 0.0
    peak=curve[0]; mdd=0.0
    for x in curve:
        peak=max(peak,x); mdd=min(mdd,x/peak-1)
    cagr=curve[-1]**(365.25/max(1,len(curve)-1))-1
    return {"final_equity":eq,"cumulative_return":eq-1,"cagr":cagr,"sharpe":sharpe,"max_drawdown":mdd,"curve":curve}


def main():
    dates,books,cache_hashes=load_local_cache()
    split=int(math.floor(len(dates)*0.7))
    oos=dates[split:]
    if oos[0]!=OOS_EXPECTED_START or oos[-1]!=OOS_EXPECTED_END or len(oos)!=530:
        raise AssertionError("OOS identity mismatch")

    baseline=run(dates,books,split,SYMBOLS,BASE_COST)
    independent_match=abs(baseline["final_equity"]-TARGET_EQUITY)<=TOL

    cost_stress={}
    for mult in (1.0,1.5,2.0):
        r=run(dates,books,split,SYMBOLS,BASE_COST*mult)
        cost_stress[f"{mult:.1f}x"]= {k:v for k,v in r.items() if k!="curve"}

    mid=len(baseline["curve"])//2
    halves={
      "first_half": {
        "cumulative_return": baseline["curve"][mid-1]-1,
      },
      "second_half": {
        "cumulative_return": baseline["curve"][-1]/baseline["curve"][mid-1]-1,
      }
    }

    leave_one_out={}
    for removed in SYMBOLS:
        u=[s for s in SYMBOLS if s!=removed]
        r=run(dates,books,split,u,BASE_COST)
        leave_one_out[removed]={k:v for k,v in r.items() if k!="curve"}

    result={
      "experiment_id":"HARMONY-INFRA-0008",
      "validation_of":"HARMONY-FIN-0003",
      "independent_reconstruction":{
        "final_equity":baseline["final_equity"],
        "target_final_equity":TARGET_EQUITY,
        "absolute_difference":abs(baseline["final_equity"]-TARGET_EQUITY),
        "exact_within_tolerance":independent_match,
        "tolerance":TOL,
      },
      "cost_stress":cost_stress,
      "oos_half_ablation":halves,
      "leave_one_out":leave_one_out,
      "cache":{
        "file_count":len(cache_hashes),
        "aggregate_manifest_sha256":digest_bytes(json.dumps(sorted(cache_hashes),separators=(",",":"),ensure_ascii=False).encode()),
      },
      "integrity":{
        "same_fixed_universe":True,
        "same_signal_rule":True,
        "same_oos_period":True,
        "same_base_cost":True,
        "parameters_not_optimized":True,
        "no_new_candidates":True,
      }
    }

    out=Path("artifacts/HARMONY-INFRA-0008")
    out.mkdir(parents=True,exist_ok=True)
    (out/"validation.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print(json.dumps(result,indent=2,sort_keys=True))


if __name__=="__main__":
    main()
