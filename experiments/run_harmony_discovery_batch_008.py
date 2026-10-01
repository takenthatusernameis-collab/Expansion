import csv, hashlib, json, math, statistics
from datetime import datetime, timezone
from pathlib import Path

SYMBOLS = ["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
ROOT = Path("data/cache/binance/futures_um/deep_history_2019")
OUT = Path("artifacts/HARMONY-DISCOVERY-BATCH-008")
END = "2025-10-31"
DISCOVERY_END = "2024-05-21"
OOS_START = "2024-05-22"
FEE = 0.0006
SLIP = 0.0005
DEEP_CAPACITY = 3

CANDIDATES = [
    "HARMONY-FIN-0040",
    "HARMONY-FIN-0044",
    "HARMONY-FIN-0048",
    "HARMONY-FIN-0049",
    "HARMONY-FIN-0050",
]

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def parse_zip(path):
    import zipfile
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise RuntimeError(f"unexpected archive members: {path}")
        return list(csv.reader(z.open(names[0]).read().decode("utf-8").splitlines()))

def load_panel():
    px = {s: {} for s in SYMBOLS}
    qv = {s: {} for s in SYMBOLS}
    funding = {s: {} for s in SYMBOLS}
    for s in SYMBOLS:
        for path in sorted((ROOT/"klines"/s/"1d").glob(f"{s}-1d-*.zip")):
            for row in parse_zip(path):
                if row and row[0].isdigit():
                    d = datetime.fromtimestamp(int(row[0])/1000.0, timezone.utc).date().isoformat()
                    if d <= END:
                        px[s][d] = float(row[4])
                        qv[s][d] = float(row[7])
        fp = ROOT/"funding_gateway"/f"{s}-2019-2025-10.json"
        for row in json.loads(fp.read_text()):
            d = datetime.fromtimestamp(int(row["fundingTime"])/1000.0, timezone.utc).date().isoformat()
            if d <= END:
                funding[s].setdefault(d, []).append(float(row["fundingRate"]))
    dates = sorted(set.intersection(*(set(px[s]) for s in SYMBOLS)))
    if dates[0] != "2020-07-10" or dates[-1] != END or len(dates) != 1935:
        raise RuntimeError(f"unexpected common panel {dates[:1]}..{dates[-1:]} n={len(dates)}")
    return dates, px, qv, funding

def returns_for(dates, px):
    r = {s: {} for s in SYMBOLS}
    for s in SYMBOLS:
        for i, d in enumerate(dates):
            r[s][d] = 0.0 if i == 0 else px[s][d]/px[s][dates[i-1]] - 1.0
    return r

def top_bottom(values):
    ordered = sorted(values, key=lambda x: (x[1], x[0]))
    if len(ordered) != len(SYMBOLS):
        raise RuntimeError("ranking universe mismatch")
    w = {s: 0.0 for s in SYMBOLS}
    for _, s in ordered[:3]:
        w[s] = 1.0/6.0
    for _, s in ordered[-3:]:
        w[s] = -1.0/6.0
    return w

def skew(values):
    n = len(values)
    mean = sum(values)/n
    m2 = sum((x-mean)**2 for x in values)/n
    m3 = sum((x-mean)**3 for x in values)/n
    if m2 <= 0.0:
        return 0.0
    return math.sqrt(n*(n-1))/(n-2) * m3/(m2**1.5)

def beta_down(asset, btc):
    pairs = [(a,b) for a,b in zip(asset, btc) if b < 0.0]
    if len(pairs) < 10:
        return None
    ma = sum(a for a,_ in pairs)/len(pairs)
    mb = sum(b for _,b in pairs)/len(pairs)
    varb = sum((b-mb)**2 for _,b in pairs)/len(pairs)
    if varb <= 0.0:
        return None
    cov = sum((a-ma)*(b-mb) for a,b in pairs)/len(pairs)
    return cov/varb

def month_key(d): return d[:7]
def week_key(d):
    x = datetime.fromisoformat(d).date()
    y,w,_ = x.isocalendar()
    return f"{y:04d}-W{w:02d}"

def first_in_period(dates):
    seen = set()
    out = set()
    for d in dates:
        key = (month_key(d), "m")
        if key not in seen:
            out.add(d); seen.add(key)
    seen = set()
    for d in dates:
        key = (week_key(d), "w")
        if key not in seen:
            out.add(d); seen.add(key)
    return out

def targets_for(candidate, dates, px, qv, rets):
    targets = {}
    for i,d in enumerate(dates):
        if candidate == "HARMONY-FIN-0040":
            # First common day of month; formation ends at the previous common month-end.
            if i == 0 or month_key(d) == month_key(dates[i-1]):
                continue
            prev = i-1
            while prev > 0 and month_key(dates[prev-1]) == month_key(dates[prev]):
                prev -= 1
            if prev < 62:
                continue
            vals=[]
            for s in SYMBOLS:
                vals.append((s, skew([rets[s][dates[j]] for j in range(prev-62, prev+1)])))
            targets[d]=top_bottom([(s,v) for s,v in vals])
        elif candidate == "HARMONY-FIN-0044":
            # End-of-day signal uses the immediately completed day and a fixed 30-day focal history.
            if i < 61:
                continue
            vals=[]
            for s in SYMBOLS:
                t=i-1
                base=[qv[s][dates[j]] for j in range(t-30,t)]
                mean_base=sum(base)/30.0
                if mean_base <= 0: continue
                surprise=qv[s][dates[t]]/mean_base - 1.0
                prior=[]
                for k in range(t-30,t):
                    prev30=[qv[s][dates[j]] for j in range(k-30,k)]
                    m=sum(prev30)/30.0
                    prior.append(qv[s][dates[k]]/m - 1.0 if m>0 else 0.0)
                sd=statistics.pstdev(prior)
                score=surprise/sd if sd>0 else 0.0
                vals.append((s,score))
            if len(vals)==len(SYMBOLS):
                targets[d]=top_bottom(vals)
        elif candidate == "HARMONY-FIN-0048":
            if i < 21 or (i == 0 or d[:10] == ""):
                continue
            # First common day of each ISO week; use prior 21 completed observations.
            if i > 0 and week_key(dates[i-1]) == week_key(d):
                continue
            vals=[]
            for s in SYMBOLS:
                vals.append((s,max(rets[s][dates[j]] for j in range(i-21,i))))
            targets[d]=top_bottom(vals)
        elif candidate == "HARMONY-FIN-0049":
            if i == 0 or (i < 31 and month_key(dates[i-1]) != month_key(d)):
                continue
            if i == 0 or month_key(dates[i-1]) == month_key(d):
                continue
            if i < 30: continue
            vals=[]
            for s in SYMBOLS:
                score=sum(abs(rets[s][dates[j]])/max(qv[s][dates[j]],1e-300) for j in range(i-30,i))/30.0
                vals.append((s,score))
            targets[d]=top_bottom(vals)
        elif candidate == "HARMONY-FIN-0050":
            if i < 60 or (i > 0 and month_key(dates[i-1]) == month_key(d)):
                continue
            vals=[]
            btc=[rets["BTCUSDT"][dates[j]] for j in range(i-60,i)]
            for s in SYMBOLS:
                v=beta_down([rets[s][dates[j]] for j in range(i-60,i)], btc)
                if v is None:
                    vals=[]
                    break
                vals.append((s,v))
            if len(vals)==len(SYMBOLS):
                targets[d]=top_bottom(vals)
        else:
            raise ValueError(candidate)
    return targets

def simulate(dates, px, funding, targets, cost_mult=1.0, funding_neutral=False):
    eq=1.0
    prev={s:0.0 for s in SYMBOLS}
    curve=[]
    turnover=0.0
    rebalances=0
    funding_sum=0.0
    for i,d in enumerate(dates):
        if not funding_neutral:
            for s in SYMBOLS:
                for rate in funding[s].get(d,[]):
                    pnl=-prev[s]*rate
                    eq*=1.0+pnl
                    funding_sum+=pnl
        if i>0:
            pd=dates[i-1]
            eq*=1.0+sum(prev[s]*(px[s][d]/px[s][pd]-1.0) for s in SYMBOLS)
        target=targets.get(d)
        if target is not None:
            delta=sum(abs(target[s]-prev[s]) for s in SYMBOLS)
            eq*=max(0.0,1.0-(FEE+SLIP)*cost_mult*delta)
            turnover+=delta/2.0
            rebalances+=1
            prev=target.copy()
        curve.append(eq)
    liq=sum(abs(v) for v in prev.values())
    eq*=max(0.0,1.0-(FEE+SLIP)*cost_mult*liq)
    curve[-1]=eq
    return {"equity":curve,"turnover":turnover,"rebalance_count":rebalances,"funding_pnl_sum":funding_sum}

def metrics(curve):
    rr=[curve[i]/curve[i-1]-1.0 for i in range(1,len(curve))]
    sd=statistics.stdev(rr) if len(rr)>1 else 0.0
    sharpe=statistics.mean(rr)/sd*math.sqrt(365.25) if sd else 0.0
    peak=curve[0]; mdd=0.0
    for x in curve:
        peak=max(peak,x); mdd=min(mdd,x/peak-1.0)
    years=max((len(curve)-1)/365.25,1e-12)
    return {"final_equity":curve[-1],"cumulative_return":curve[-1]-1.0,"cagr":curve[-1]**(1.0/years)-1.0,"sharpe":sharpe,"max_drawdown":mdd,"observations":len(curve)}

def segment(dates, curve, start, end=None):
    idx=[i for i,d in enumerate(dates) if d>=start and (end is None or d<=end)]
    if not idx: raise RuntimeError("empty segment")
    base=curve[idx[0]-1] if idx[0]>0 else 1.0
    c=[1.0]+[curve[i]/base for i in idx]
    out=metrics(c); out.update({"start":dates[idx[0]],"end":dates[idx[-1]]})
    return out

def halves(dates, curve, start, end=None):
    idx=[i for i,d in enumerate(dates) if d>=start and (end is None or d<=end)]
    mid=max(1,len(idx)//2)
    def p(xs):
        base=curve[xs[0]-1] if xs[0]>0 else 1.0
        c=[1.0]+[curve[i]/base for i in xs]
        out=metrics(c); out.update({"start":dates[xs[0]],"end":dates[xs[-1]]})
        return out
    return {"first_half":p(idx[:mid]),"second_half":p(idx[mid:])}

def run_candidate(candidate, dates, px, qv, funding, rets):
    targets=targets_for(candidate,dates,px,qv,rets)
    full={}; cheap={}
    for cm in (1.0,2.0):
        sim=simulate(dates,px,funding,targets,cost_mult=cm)
        full[f"{cm:.1f}x"]={
            "metrics":metrics(sim["equity"]),
            "oos":segment(dates,sim["equity"],OOS_START),
            "oos_halves":halves(dates,sim["equity"],OOS_START),
            "discovery":segment(dates,sim["equity"],"2020-07-10",DISCOVERY_END),
            "discovery_halves":halves(dates,sim["equity"],"2020-07-10",DISCOVERY_END),
            "turnover":sim["turnover"],
            "rebalance_count":sim["rebalance_count"],
            "funding_pnl_sum":sim["funding_pnl_sum"],
        }
    neutral=simulate(dates,px,funding,targets,cost_mult=1.0,funding_neutral=True)
    full["funding_neutral"]={"oos":segment(dates,neutral["equity"],OOS_START),"metrics":metrics(neutral["equity"])}
    d=full["1.0x"]["discovery"]; d2=full["2.0x"]["discovery"]
    passed=(full["1.0x"]["discovery"]["cumulative_return"]>0 and
            full["1.0x"]["discovery"]["sharpe"]>0 and
            full["1.0x"]["rebalance_count"]>=20)
    cheap={
        "candidate":candidate,
        "passed_gate":passed,
        "base":d,
        "two_x_cost":d2,
        "rebalance_count":full["1.0x"]["rebalance_count"],
        "funding_pnl_sum":full["1.0x"]["funding_pnl_sum"],
    }
    return {"targets":len(targets),"cheap":cheap,"deep":full}

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    dates,px,qv,funding=load_panel()
    rets=returns_for(dates,px)
    results={}
    for candidate in CANDIDATES:
        results[candidate]=run_candidate(candidate,dates,px,qv,funding,rets)
    passed=[c for c in CANDIDATES if results[c]["cheap"]["passed_gate"]]
    ranked=sorted(passed,key=lambda c:(results[c]["cheap"]["two_x_cost"]["sharpe"],c),reverse=True)
    selected=ranked[:DEEP_CAPACITY]

    # Benchmarks use the same accounting path as candidate simulations.
    btc_targets={d:{s:(1.0 if s=="BTCUSDT" else 0.0) for s in SYMBOLS} for d in dates if d[:7]!= ""}
    ew_targets={d:{s:1.0/len(SYMBOLS) for s in SYMBOLS} for d in dates if d[:7]!= ""}
    btc=simulate(dates,px,funding,btc_targets,1.0)
    ew=simulate(dates,px,funding,ew_targets,1.0)

    manifest={
      "batch_id":"HARMONY-DISCOVERY-BATCH-008",
      "dataset_cache_key":"harmony-binance-um-deep-history-2019-2025-10-v1-36777989764",
      "panel":{"start":dates[0],"end":dates[-1],"rows":len(dates),"symbols":SYMBOLS},
      "discovery_end":DISCOVERY_END,"oos_start":OOS_START,"end":END,
      "candidates":CANDIDATES,
      "cheap_gate":{"minimum_rebalances":20,"minimum_cumulative_return_after_costs":0.0,"minimum_sharpe_after_costs":0.0},
      "selection_rule":"passing candidates ranked by 2x-cost discovery Sharpe, top 3 only",
      "deep_parameter_search":False,
      "holdout_access":False,
    }
    mraw=(json.dumps(manifest,sort_keys=True,indent=2)+"\n").encode()
    (OUT/"input-manifest.json").write_bytes(mraw)
    payload={
      "batch_id":"HARMONY-DISCOVERY-BATCH-008",
      "input_manifest_sha256":sha(mraw),
      "cheap_screen":{c:results[c]["cheap"] for c in CANDIDATES},
      "selected_for_deep":selected,
      "deep_results":{c:results[c]["deep"] for c in selected},
      "benchmarks":{
        "BTCUSDT_buy_and_hold":{"metrics":metrics(btc["equity"]),"oos":segment(dates,btc["equity"],OOS_START)},
        "same_universe_equal_weight_long_only":{"metrics":metrics(ew["equity"]),"oos":segment(dates,ew["equity"],OOS_START)}
      },
      "integrity":{"holdout_access":False,"parameter_search":False,"universe_search":False,"direction_search":False,"candidate_mutation":False}
    }
    raw=(json.dumps(payload,sort_keys=True,indent=2)+"\n").encode()
    result_sha=sha(raw)
    (OUT/"HARMONY-DISCOVERY-BATCH-008-RESULT.json").write_bytes(raw)
    (OUT/"HARMONY-DISCOVERY-BATCH-008-SUMMARY.json").write_text(json.dumps({
      "batch_id":payload["batch_id"],"result_sha256":result_sha,
      "passed_cheap":passed,"selected_for_deep":selected,
      "cheap_sharpes_2x_cost":{c:results[c]["cheap"]["two_x_cost"]["sharpe"] for c in CANDIDATES}
    },sort_keys=True,indent=2)+"\n")
    print(json.dumps({"batch_id":payload["batch_id"],"passed_cheap":passed,"selected_for_deep":selected,"result_sha256":result_sha},indent=2))

if __name__=="__main__":
    main()
