import csv
import hashlib
import json
import math
import statistics
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SYMBOLS = ["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
ROOT = Path("data/cache/binance/futures_um/deep_history_2019")
OUT = Path("artifacts/HARMONY-FIN-0056-DURABILITY-001")
END = "2025-10-31"
OOS_START = "2024-05-22"
FEE = 0.0006
SLIP = 0.0005
EXPECTED = {"cumulative_return": 0.28467078513220323, "sharpe": 1.035538238338156}

def parse_zip(path):
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
                funding[s].setdefault(d, []).append(float(row["fundingRate"]))
    dates = sorted(set.intersection(*(set(px[s]) for s in SYMBOLS)))
    if (dates[0], dates[-1], len(dates)) != ("2020-07-10", END, 1935):
        raise RuntimeError("panel mismatch")
    return dates, px, funding

def daily_returns(dates, px):
    return {s:{d:(0.0 if i == 0 else px[s][d]/px[s][dates[i-1]]-1.0) for i,d in enumerate(dates)} for s in SYMBOLS}

def month_key(d):
    return d[:7]

def kurtosis(values):
    n = len(values)
    mean = sum(values)/n
    m2 = sum((x-mean)**2 for x in values)/n
    if m2 <= 0:
        return 0.0
    m4 = sum((x-mean)**4 for x in values)/n
    return m4/(m2*m2)

def build_targets(dates, rets):
    out = {}
    for i, d in enumerate(dates):
        if i < 60 or (i > 0 and month_key(dates[i-1]) == month_key(d)):
            continue
        scores = [(kurtosis([rets[s][dates[j]] for j in range(i-60,i)]), s) for s in SYMBOLS]
        scores.sort(key=lambda x:(x[0], x[1]))
        t = {s:0.0 for s in SYMBOLS}
        for _, s in scores[:3]:
            t[s] = -1/6
        for _, s in scores[-3:]:
            t[s] = 1/6
        out[d] = t
    return out

def simulate(dates, px, funding, targets, cost_mult=1.0):
    eq = 1.0
    prev = {s:0.0 for s in SYMBOLS}
    curve = []
    turnover = 0.0
    for i, d in enumerate(dates):
        for s in SYMBOLS:
            for rate in funding[s].get(d, []):
                eq *= 1.0 - prev[s]*rate
        if i > 0:
            pd = dates[i-1]
            eq *= 1.0 + sum(prev[s]*(px[s][d]/px[s][pd]-1.0) for s in SYMBOLS)
        if d in targets:
            t = targets[d]
            delta = sum(abs(t[s]-prev[s]) for s in SYMBOLS)
            eq *= max(0.0, 1.0-(FEE+SLIP)*cost_mult*delta)
            turnover += delta/2.0
            prev = t.copy()
        curve.append(eq)
    liquidation = sum(abs(v) for v in prev.values())
    eq *= max(0.0, 1.0-(FEE+SLIP)*cost_mult*liquidation)
    curve[-1] = eq
    return curve, turnover

def metrics(curve):
    rr = [curve[i]/curve[i-1]-1.0 for i in range(1, len(curve))]
    sd = statistics.stdev(rr) if len(rr) > 1 else 0.0
    sharpe = statistics.mean(rr)/sd*math.sqrt(365.25) if sd else 0.0
    peak = curve[0]
    mdd = 0.0
    for x in curve:
        peak = max(peak, x)
        mdd = min(mdd, x/peak-1.0)
    years = max((len(curve)-1)/365.25, 1e-12)
    return {"final_equity":curve[-1], "cumulative_return":curve[-1]-1.0, "cagr":curve[-1]**(1/years)-1.0, "sharpe":sharpe, "max_drawdown":mdd, "observations":len(curve)}

def oos_indices(dates):
    return [i for i,d in enumerate(dates) if d >= OOS_START]

def segment(dates, curve, idxs):
    base = curve[idxs[0]-1] if idxs[0] > 0 else 1.0
    c = [1.0] + [curve[i]/base for i in idxs]
    m = metrics(c)
    m.update(start=dates[idxs[0]], end=dates[idxs[-1]], observations=len(idxs))
    return m

def residual_series(y, x1, x2):
    n = len(y)
    X = [[1.0, x1[i], x2[i]] for i in range(n)]
    A = [[sum(X[i][a]*X[i][b] for i in range(n)) for b in range(3)] for a in range(3)]
    b = [sum(X[i][a]*y[i] for i in range(n)) for a in range(3)]
    M = [A[i] + [b[i]] for i in range(3)]
    for k in range(3):
        p = max(range(k,3), key=lambda i: abs(M[i][k]))
        M[k], M[p] = M[p], M[k]
        pivot = M[k][k]
        if abs(pivot) < 1e-15:
            return list(y)
        M[k] = [v/pivot for v in M[k]]
        for i in range(3):
            if i == k:
                continue
            f = M[i][k]
            M[i] = [M[i][j] - f*M[k][j] for j in range(4)]
    beta = [M[i][3] for i in range(3)]
    return [y[i]-(beta[0]+beta[1]*x1[i]+beta[2]*x2[i]) for i in range(n)]

def curve_from_returns(rr):
    c = [1.0]
    for r in rr:
        c.append(c[-1]*(1.0+r))
    return c

def groups_by_key(dates, idxs, key_fn):
    out = {}
    for pos, i in enumerate(idxs):
        out.setdefault(key_fn(dates[i]), []).append(pos)
    return out

def group_metrics(dates, curve, idxs, groups):
    out = {}
    for k, positions in groups.items():
        real = [idxs[p] for p in positions]
        out[k] = segment(dates, curve, real)
    return out

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    dates, px, funding = load_panel()
    rets = daily_returns(dates, px)
    targets = build_targets(dates, rets)

    traces = {}
    for mult in (1.0, 2.0, 3.0, 4.0):
        curve, turnover = simulate(dates, px, funding, targets, mult)
        traces[str(mult)] = {"curve":curve, "turnover":turnover}

    idxs = oos_indices(dates)
    base = segment(dates, traces["1.0"]["curve"], idxs)
    reproduction = {
        "expected":EXPECTED,
        "observed":{"cumulative_return":base["cumulative_return"], "sharpe":base["sharpe"]},
        "delta":{"cumulative_return":base["cumulative_return"]-EXPECTED["cumulative_return"], "sharpe":base["sharpe"]-EXPECTED["sharpe"]},
        "pass": abs(base["cumulative_return"]-EXPECTED["cumulative_return"]) <= 1e-12 and abs(base["sharpe"]-EXPECTED["sharpe"]) <= 1e-12
    }

    mid = len(idxs)//2
    quarters = [idxs[:len(idxs)//4], idxs[len(idxs)//4:2*len(idxs)//4], idxs[2*len(idxs)//4:3*len(idxs)//4], idxs[3*len(idxs)//4:]]
    halves = [idxs[:mid], idxs[mid:]]
    temporal = {
        "first_half":segment(dates, traces["1.0"]["curve"], halves[0]),
        "second_half":segment(dates, traces["1.0"]["curve"], halves[1]),
        "quarters":[segment(dates, traces["1.0"]["curve"], q) for q in quarters]
    }
    temporal_distributed = (
        temporal["first_half"]["cumulative_return"] > 0 and temporal["first_half"]["sharpe"] > 0 and
        temporal["second_half"]["cumulative_return"] > 0 and temporal["second_half"]["sharpe"] > 0 and
        sum(q["cumulative_return"] > 0 for q in temporal["quarters"]) >= 3
    )

    rolling_results = {}
    oos_curve = [1.0] + [traces["1.0"]["curve"][i]/traces["1.0"]["curve"][idxs[0]-1] for i in idxs]
    for w in (90,180):
        vals = [metrics(oos_curve[i-w:i+1]) for i in range(w, len(oos_curve))]
        rolling_results[str(w)] = {
            "count":len(vals),
            "median_sharpe":statistics.median(v["sharpe"] for v in vals),
            "positive_fraction":sum(v["sharpe"]>0 for v in vals)/len(vals),
            "min_sharpe":min(v["sharpe"] for v in vals),
            "max_sharpe":max(v["sharpe"] for v in vals)
        }

    btc_targets = {d:{s:(1.0 if s=="BTCUSDT" else 0.0) for s in SYMBOLS} for d in dates}
    ew_targets = {d:{s:1.0/len(SYMBOLS) for s in SYMBOLS} for d in dates}
    btc_curve,_ = simulate(dates, px, funding, btc_targets, 1.0)
    ew_curve,_ = simulate(dates, px, funding, ew_targets, 1.0)

    strategy_rr = [traces["1.0"]["curve"][i]/traces["1.0"]["curve"][i-1]-1.0 for i in idxs]
    btc_rr = [btc_curve[i]/btc_curve[i-1]-1.0 for i in idxs]
    ew_rr = [ew_curve[i]/ew_curve[i-1]-1.0 for i in idxs]
    residual = residual_series(strategy_rr, btc_rr, ew_rr)
    residual_full = metrics(curve_from_returns(residual))
    qn = len(residual)//4
    residual_quarters = [metrics(curve_from_returns(residual[:qn])), metrics(curve_from_returns(residual[qn:2*qn])), metrics(curve_from_returns(residual[2*qn:3*qn])), metrics(curve_from_returns(residual[3*qn:]))]
    residual_persistent = residual_full["cumulative_return"] > 0 and sum(x["cumulative_return"] > 0 for x in residual_quarters) >= 3

    date_list = [dates[i] for i in idxs]
    by_year = group_metrics(dates, traces["1.0"]["curve"], idxs, groups_by_key(dates, idxs, lambda d:d[:4]))
    by_quarter = group_metrics(dates, traces["1.0"]["curve"], idxs, groups_by_key(dates, idxs, lambda d:f"{d[:4]}-Q{((int(d[5:7])-1)//3)+1}"))
    best_year = max(by_year, key=lambda k:by_year[k]["cumulative_return"])
    best_quarter = max(by_quarter, key=lambda k:by_quarter[k]["cumulative_return"])
    timing = {
        "by_year":by_year,
        "by_quarter":by_quarter,
        "best_year":best_year,
        "best_quarter":best_quarter,
        "best_year_profit_fraction":by_year[best_year]["cumulative_return"]/base["cumulative_return"],
        "best_quarter_profit_fraction":by_quarter[best_quarter]["cumulative_return"]/base["cumulative_return"]
    }

    cost_stress = {}
    for mult in (1.0,2.0,3.0,4.0):
        cost_stress[str(mult)] = segment(dates, traces[str(mult)]["curve"], idxs)
    cost_preserved = cost_stress["2.0"]["cumulative_return"] > 0
    not_concentrated = timing["best_year_profit_fraction"] <= 0.75 and timing["best_quarter_profit_fraction"] <= 0.50

    if temporal_distributed and residual_persistent and cost_preserved and not_concentrated:
        status = "DURABILITY_SUPPORTED"
    elif (
        temporal["first_half"]["cumulative_return"] <= 0 and temporal["second_half"]["cumulative_return"] <= 0
        or (residual_full["cumulative_return"] <= 0 and cost_stress["2.0"]["cumulative_return"] <= 0)
        or timing["best_quarter_profit_fraction"] > 0.85
    ):
        status = "DURABILITY_NOT_SUPPORTED"
    else:
        status = "DURABILITY_MIXED"

    manifest = {
        "audit_id":"HARMONY-FIN-0056-DURABILITY-001",
        "candidate":"HARMONY-FIN-0056",
        "cache_key":"harmony-binance-um-deep-history-2019-2025-10-v1-36777989764",
        "panel":{"start":dates[0],"end":dates[-1],"rows":len(dates),"symbols":SYMBOLS},
        "oos_start":OOS_START,
        "no_parameter_search":True,
        "holdout_access":False
    }
    mr=(json.dumps(manifest,sort_keys=True,indent=2)+"\n").encode()
    (OUT/"input-manifest.json").write_bytes(mr)
    payload={
        "audit_id":"HARMONY-FIN-0056-DURABILITY-001",
        "status":status,
        "input_manifest_sha256":hashlib.sha256(mr).hexdigest(),
        "reproduction":reproduction,
        "temporal":temporal,
        "temporal_distributed":temporal_distributed,
        "rolling":rolling_results,
        "factor_residual":{"full_period":residual_full,"quarters":residual_quarters,"persistent":residual_persistent},
        "cost_stress":cost_stress,
        "cost_preserved":cost_preserved,
        "timing":timing,
        "not_extremely_concentrated":not_concentrated,
        "integrity":{"fixed_definition":True,"parameter_search":False,"universe_search":False,"direction_search":False,"holdout_access":False,"candidate_mutation":False}
    }
    raw=(json.dumps(payload,sort_keys=True,indent=2)+"\n").encode()
    result_sha=hashlib.sha256(raw).hexdigest()
    (OUT/"HARMONY-FIN-0056-DURABILITY-001-RESULT.json").write_bytes(raw)
    (OUT/"HARMONY-FIN-0056-DURABILITY-001-SUMMARY.json").write_text(json.dumps({
        "audit_id":payload["audit_id"],
        "status":status,
        "result_sha256":result_sha,
        "baseline_oos_sharpe":base["sharpe"],
        "second_half_sharpe":temporal["second_half"]["sharpe"],
        "residual_sharpe":residual_full["sharpe"],
        "four_x_cost_sharpe":cost_stress["4.0"]["sharpe"],
        "temporal_distributed":temporal_distributed,
        "residual_persistent":residual_persistent,
        "not_extremely_concentrated":not_concentrated
    },sort_keys=True,indent=2)+"\n")
    print(json.dumps({"status":status,"result_sha256":result_sha},indent=2))

if __name__ == "__main__":
    main()
