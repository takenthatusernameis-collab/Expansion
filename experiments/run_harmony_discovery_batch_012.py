import csv, hashlib, json, math, statistics
from datetime import datetime, timezone
from pathlib import Path

SYMBOLS = ["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
ROOT = Path("data/cache/binance/futures_um/deep_history_2019")
OUT = Path("artifacts/HARMONY-DISCOVERY-BATCH-012")
END = "2025-10-31"
DISCOVERY_END = "2024-05-21"
OOS_START = "2024-05-22"
FEE = 0.0006
SLIP = 0.0005
DEEP_CAPACITY = 2
CANDIDATES = ["HARMONY-FIN-0062","HARMONY-FIN-0063","HARMONY-FIN-0064","HARMONY-FIN-0065"]

def parse_zip(path):
    import zipfile
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise RuntimeError(f"unexpected archive shape: {path}")
        return list(csv.reader(z.open(names[0]).read().decode("utf-8").splitlines()))

def load_panel():
    px = {s:{} for s in SYMBOLS}
    funding = {s:{} for s in SYMBOLS}
    for s in SYMBOLS:
        for p in sorted((ROOT/"klines"/s/"1d").glob(f"{s}-1d-*.zip")):
            for row in parse_zip(p):
                if row and row[0].isdigit():
                    d = datetime.fromtimestamp(int(row[0])/1000.0, timezone.utc).date().isoformat()
                    if d <= END:
                        px[s][d] = float(row[4])
        fp = ROOT/"funding_gateway"/f"{s}-2019-2025-10.json"
        for row in json.loads(fp.read_text()):
            d = datetime.fromtimestamp(int(row["fundingTime"])/1000.0, timezone.utc).date().isoformat()
            if d <= END:
                funding[s].setdefault(d,[]).append(float(row["fundingRate"]))
    dates = sorted(set.intersection(*(set(px[s]) for s in SYMBOLS)))
    if (dates[0],dates[-1],len(dates)) != ("2020-07-10",END,1935):
        raise RuntimeError(f"unexpected common panel {dates[:1]}..{dates[-1:]} n={len(dates)}")
    return dates, px, funding

def daily_returns(dates, px):
    return {s:{d:(0.0 if i==0 else px[s][d]/px[s][dates[i-1]]-1.0) for i,d in enumerate(dates)} for s in SYMBOLS}

def month_key(d): return d[:7]
def week_key(d):
    x = datetime.fromisoformat(d).date()
    y,w,_ = x.isocalendar()
    return f"{y:04d}-W{w:02d}"

def first_of_period(dates, key_fn):
    out = {}
    prev = None
    for d in dates:
        k = key_fn(d)
        if k != prev:
            out[d] = k
            prev = k
    return out

def percentile(values, q):
    xs = sorted(values)
    if not xs:
        raise ValueError("empty values")
    if len(xs) == 1:
        return xs[0]
    h = (len(xs)-1)*q
    lo = int(math.floor(h)); hi = int(math.ceil(h))
    if lo == hi:
        return xs[lo]
    return xs[lo] + (xs[hi]-xs[lo])*(h-lo)

def rank_weights(scores, long_low):
    if len(scores) != len(SYMBOLS) or set(s for _,s in scores) != set(SYMBOLS):
        raise RuntimeError("ranking universe mismatch")
    ordered = sorted(scores, key=lambda x:(x[0],x[1]))
    w = {s:0.0 for s in SYMBOLS}
    if long_low:
        longs = [s for _,s in ordered[:3]]
        shorts = [s for _,s in ordered[-3:]]
    else:
        longs = [s for _,s in ordered[-3:]]
        shorts = [s for _,s in ordered[:3]]
    for s in longs: w[s] = 1/6
    for s in shorts: w[s] = -1/6
    if abs(sum(abs(w[s]) for s in SYMBOLS)-1.0) > 1e-12:
        raise RuntimeError("gross exposure invariant")
    return w

def salience_score(series, all_daily):
    market = [sum(all_daily[s][d] for s in SYMBOLS)/len(SYMBOLS) for d in series["dates"]]
    asset = series["asset"]
    states = []
    for r,m in zip(asset,market):
        sal = abs(r-m)/(abs(r)+abs(m)+0.1)
        states.append((sal,r))
    states.sort(key=lambda x:(-x[0],x[1]))
    delta = 0.7
    weights = [delta**k for k in range(len(states))]
    z = sum(weights)
    return sum((weights[k]/z)*states[k][1] for k in range(len(states))) - sum(asset)/len(asset)

def build_targets(candidate, dates, px, rets):
    targets = {}
    if candidate == "HARMONY-FIN-0062":
        starts = first_of_period(dates, week_key)
        for i,d in enumerate(dates):
            if d not in starts or i < 90:
                continue
            scores=[]
            for s in SYMBOLS:
                q05 = percentile([rets[s][dates[j]] for j in range(i-90,i)],0.05)
                scores.append((-q05,s))
            targets[d] = rank_weights(scores, long_low=False)
    elif candidate == "HARMONY-FIN-0063":
        starts = first_of_period(dates, month_key)
        for i,d in enumerate(dates):
            if d not in starts or i < 20:
                continue
            ds = dates[i-20:i]
            scores=[]
            for s in SYMBOLS:
                payload = {"dates":ds,"asset":[rets[s][x] for x in ds]}
                scores.append((salience_score(payload, rets),s))
            targets[d] = rank_weights(scores, long_low=True)
    elif candidate == "HARMONY-FIN-0064":
        for i,d in enumerate(dates):
            if i < 7*20:
                continue
            wd = datetime.fromisoformat(d).date().weekday()
            idx = []
            for j in range(i-1,-1,-1):
                if datetime.fromisoformat(dates[j]).date().weekday() == wd:
                    idx.append(j)
                    if len(idx)==20:
                        break
            if len(idx) < 20:
                continue
            scores=[]
            for s in SYMBOLS:
                scores.append((sum(rets[s][dates[j]] for j in idx)/20.0,s))
            targets[d] = rank_weights(scores, long_low=False)
    elif candidate == "HARMONY-FIN-0065":
        starts = first_of_period(dates, month_key)
        for i,d in enumerate(dates):
            if d not in starts or i < 30:
                continue
            rr = [sum(rets[s][dates[j]] for j in range(i-30,i)) for s in SYMBOLS]
            # Correlation of asset daily returns with time-to-window-end: oldest=30,...,newest=1.
            x = list(range(30,0,-1))
            mx = sum(x)/30
            vx = sum((v-mx)**2 for v in x)
            scores=[]
            for s in SYMBOLS:
                y = [rets[s][dates[j]] for j in range(i-30,i)]
                my = sum(y)/30
                vy = sum((v-my)**2 for v in y)
                cov = sum((a-my)*(b-mx) for a,b in zip(y,x))
                corr = cov/math.sqrt(vy*vx) if vy > 0 and vx > 0 else 0.0
                scores.append((corr,s))
            targets[d] = rank_weights(scores, long_low=False)
    else:
        raise ValueError(candidate)
    return targets

def simulate(dates, px, funding, targets, mult=1.0, end_date=None):
    end_idx = len(dates)-1 if end_date is None else max(i for i,d in enumerate(dates) if d <= end_date)
    eq=1.0
    prev={s:0.0 for s in SYMBOLS}
    curve=[]; turnover=0.0; rebalances=0; funding_sum=0.0
    for i in range(end_idx+1):
        d=dates[i]
        for s in SYMBOLS:
            for rate in funding[s].get(d,[]):
                pnl=-prev[s]*rate
                eq*=1.0+pnl
                funding_sum += pnl
        if i>0:
            pd=dates[i-1]
            eq*=1.0+sum(prev[s]*(px[s][d]/px[s][pd]-1.0) for s in SYMBOLS)
        if d in targets:
            t=targets[d]
            delta=sum(abs(t[s]-prev[s]) for s in SYMBOLS)
            eq*=max(0.0,1.0-(FEE+SLIP)*mult*delta)
            turnover += delta/2.0
            rebalances += 1
            prev=t.copy()
        curve.append(eq)
    liq=sum(abs(v) for v in prev.values())
    eq*=max(0.0,1.0-(FEE+SLIP)*mult*liq)
    curve[-1]=eq
    return curve,turnover,rebalances,funding_sum

def metrics(curve):
    rr=[curve[i]/curve[i-1]-1.0 for i in range(1,len(curve))]
    sd=statistics.stdev(rr) if len(rr)>1 else 0.0
    sharpe=statistics.mean(rr)/sd*math.sqrt(365.25) if sd else 0.0
    peak=curve[0]; mdd=0.0
    for x in curve:
        peak=max(peak,x); mdd=min(mdd,x/peak-1.0)
    years=max((len(curve)-1)/365.25,1e-12)
    return {"final_equity":curve[-1],"cumulative_return":curve[-1]-1.0,"cagr":curve[-1]**(1/years)-1.0,"sharpe":sharpe,"max_drawdown":mdd,"observations":len(curve)}

def half_metrics(dates, curve, start):
    idx=[i for i,d in enumerate(dates) if d>=start]
    mid=len(idx)//2
    def one(xs):
        base=curve[xs[0]-1] if xs[0]>0 else 1.0
        return metrics([1.0]+[curve[i]/base for i in xs])
    return {"first_half":one(idx[:mid]),"second_half":one(idx[mid:])}

def segment(dates,curve,start):
    idx=[i for i,d in enumerate(dates) if d>=start]
    if len(curve) < len(dates):
        # Discovery traces are truncated at DISCOVERY_END; index the prefix directly.
        n = len(curve)
        m = metrics(curve)
        m.update(start=dates[0], end=dates[n-1], observations=n)
        return m
    base=curve[idx[0]-1] if idx[0]>0 else 1.0
    return metrics([1.0]+[curve[i]/base for i in idx])

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    dates,px,funding=load_panel()
    rets=daily_returns(dates,px)
    cheap={}; passed=[]
    for cid in CANDIDATES:
        tar=build_targets(cid,dates,px,rets)
        runs={}
        for mult in (1.0,2.0):
            curve,turn,rebs,fpnl=simulate(dates,px,funding,tar,mult,DISCOVERY_END)
            runs[f"{mult:.1f}x"]={"discovery":segment(dates,curve,"2020-07-10"),"turnover":turn,"rebalance_count":rebs,"funding_pnl_sum":fpnl}
        ok=(runs["1.0x"]["discovery"]["cumulative_return"]>0 and runs["1.0x"]["discovery"]["sharpe"]>0 and runs["1.0x"]["rebalance_count"]>=20)
        cheap[cid]={"passed_gate":ok,"runs":runs}
        if ok: passed.append(cid)
    passed=sorted(passed,key=lambda c:(cheap[c]["runs"]["2.0x"]["discovery"]["sharpe"],c),reverse=True)
    selected=passed[:DEEP_CAPACITY]
    deep={}
    for cid in selected:
        tar=build_targets(cid,dates,px,rets)
        runs={}
        for mult in (1.0,2.0):
            curve,turn,rebs,fpnl=simulate(dates,px,funding,tar,mult)
            runs[f"{mult:.1f}x"]={"full":metrics(curve),"oos":segment(dates,curve,OOS_START),"oos_halves":half_metrics(dates,curve,OOS_START),"turnover":turn,"rebalance_count":rebs,"funding_pnl_sum":fpnl}
        deep[cid]={"runs":runs}
    manifest={"batch_id":"HARMONY-DISCOVERY-BATCH-012","cache_key":"harmony-binance-um-deep-history-2019-2025-10-v1-36777989764","panel":{"start":dates[0],"end":dates[-1],"rows":len(dates),"symbols":SYMBOLS},"discovery_end":DISCOVERY_END,"oos_start":OOS_START,"candidates":CANDIDATES,"deep_capacity":DEEP_CAPACITY,"selection_rule":"cheap gate then descending 2x-cost discovery Sharpe","no_parameter_search":True,"holdout_access":False}
    mr=(json.dumps(manifest,sort_keys=True,indent=2)+"\n").encode()
    (OUT/"input-manifest.json").write_bytes(mr)
    payload={"batch_id":"HARMONY-DISCOVERY-BATCH-012","input_manifest_sha256":hashlib.sha256(mr).hexdigest(),"cheap_screen":cheap,"passed_cheap":passed,"selected_for_deep":selected,"deep_results":deep,"integrity":{"holdout_access":False,"parameter_search":False,"universe_search":False,"direction_search":False,"candidate_mutation":False,"oos_computed_for_nonselected":False}}
    raw=(json.dumps(payload,sort_keys=True,indent=2)+"\n").encode()
    rsha=hashlib.sha256(raw).hexdigest()
    (OUT/"HARMONY-DISCOVERY-BATCH-012-RESULT.json").write_bytes(raw)
    (OUT/"HARMONY-DISCOVERY-BATCH-012-SUMMARY.json").write_text(json.dumps({"batch_id":payload["batch_id"],"passed_cheap":passed,"selected_for_deep":selected,"result_sha256":rsha,"discovery_2x_cost_sharpe":{c:cheap[c]["runs"]["2.0x"]["discovery"]["sharpe"] for c in CANDIDATES}},sort_keys=True,indent=2)+"\n")
    print(json.dumps({"passed_cheap":passed,"selected_for_deep":selected,"result_sha256":rsha},indent=2))

if __name__=="__main__":
    main()
