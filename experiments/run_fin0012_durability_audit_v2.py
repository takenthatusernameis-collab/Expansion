import csv, hashlib, io, json, math, statistics, zipfile
from datetime import datetime, timezone
from pathlib import Path

S = ["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
ROOT = Path("data/cache/binance/futures_um/monthly")
FEE = 0.0006
SLIP = 0.0005
REBALANCE_EVERY = 7
RETURN_HORIZON = 20
BETA_WINDOW = 60
OUT = Path("artifacts/HARMONY-FIN-0012")

def day(ms):
    return datetime.fromtimestamp(ms/1000, timezone.utc).date().isoformat()

def months():
    out = []
    y, m = 2021, 1
    while (y, m) <= (2025, 10):
        out.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out

def rows(path):
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise RuntimeError(f"unexpected archive members: {path}")
        return list(csv.reader(io.StringIO(z.read(names[0]).decode("utf-8"))))

px = {s: {} for s in S}
raw_funding = {s: [] for s in S}
cache_hashes = []

for s in S:
    for y, m in months():
        kp = ROOT / "klines" / s / "1d" / f"{s}-1d-{y:04d}-{m:02d}.zip"
        fp = ROOT / "fundingRate" / s / f"{s}-fundingRate-{y:04d}-{m:02d}.zip"
        for p in (kp, fp):
            b = p.read_bytes()
            cache_hashes.append((str(p), hashlib.sha256(b).hexdigest(), len(b)))
        for r in rows(kp):
            if r and r[0].isdigit():
                px[s][day(int(r[0]))] = float(r[4])
        rr = rows(fp)
        h = {k.strip(): i for i, k in enumerate(rr[0])}
        required = {"calc_time", "funding_interval_hours", "last_funding_rate"}
        if not required.issubset(h):
            raise RuntimeError(f"funding schema mismatch: {fp}")
        for r in rr[1:]:
            if r:
                raw_funding[s].append((
                    int(r[h["calc_time"]]),
                    int(float(r[h["funding_interval_hours"]])),
                    float(r[h["last_funding_rate"]])
                ))

dates = sorted(set.intersection(*(set(px[s]) for s in S)))
assert len(dates) == 1760 and dates[0] == "2021-01-01" and dates[-1] == "2025-10-31"
split = math.floor(len(dates) * 0.70)
oos = dates[split:]
assert len(oos) == 528 and oos[0] == "2024-05-22" and oos[-1] == "2025-10-31"

funding = {s: {} for s in S}
for s in S:
    for ts, interval, rate in raw_funding[s]:
        funding[s].setdefault(day(ts), []).append((interval, rate))

def target(i):
    if i < max(RETURN_HORIZON + 1, BETA_WINDOW + 1):
        return {s: 0.0 for s in S}

    signal_idx = i - 1
    base_idx = i - RETURN_HORIZON - 1
    btc_return = px["BTCUSDT"][dates[signal_idx]] / px["BTCUSDT"][dates[base_idx]] - 1.0
    scores = []

    btc_daily = []
    for j in range(i - BETA_WINDOW, i):
        btc_daily.append(px["BTCUSDT"][dates[j]] / px["BTCUSDT"][dates[j - 1]] - 1.0)
    btc_mean = sum(btc_daily) / len(btc_daily)
    btc_var = sum((r - btc_mean) ** 2 for r in btc_daily) / len(btc_daily)

    for s in S:
        asset_return = px[s][dates[signal_idx]] / px[s][dates[base_idx]] - 1.0
        if btc_var == 0.0:
            beta = 0.0
        else:
            asset_daily = [
                px[s][dates[j]] / px[s][dates[j - 1]] - 1.0
                for j in range(i - BETA_WINDOW, i)
            ]
            asset_mean = sum(asset_daily) / len(asset_daily)
            cov = sum(
                (x - asset_mean) * (y - btc_mean)
                for x, y in zip(asset_daily, btc_daily)
            ) / len(btc_daily)
            beta = cov / btc_var
        score = asset_return - beta * btc_return
        scores.append((score, s))

    scores.sort(key=lambda z: (-z[0], z[1]))
    w = {s: 0.0 for s in S}
    for _, s in scores[:2]:
        w[s] = 0.25
    for _, s in scores[-2:]:
        w[s] = -0.25
    return w

def metrics(curve):
    rr = [curve[i] / curve[i-1] - 1.0 for i in range(1, len(curve))]
    sd = statistics.stdev(rr) if len(rr) > 1 else 0.0
    sharpe = (statistics.mean(rr) / sd) * math.sqrt(365.25) if sd else 0.0
    peak = curve[0]
    mdd = 0.0
    for x in curve:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1.0)
    cagr = curve[-1] ** (365.25 / max(1, len(curve) - 1)) - 1.0
    return {
        "final_equity": curve[-1],
        "cumulative_return": curve[-1] - 1.0,
        "cagr": cagr,
        "max_drawdown": mdd,
        "sharpe": sharpe,
        "observations": len(curve)
    }

def simulate(start, end, mode, cost_mult=1.0, apply_funding=True):
    eq = 1.0
    prev = {s: 0.0 for s in S}
    curve = []
    turnover = 0.0
    cost_sum = 0.0
    funding_sum = 0.0
    funding_events = 0

    for i in range(start, end):
        d = dates[i]

        if apply_funding:
            for s in S:
                for interval, rate in funding[s].get(d, []):
                    eq *= 1.0 - prev[s] * rate
                    funding_sum += -prev[s] * rate
                    funding_events += 1

        if i > start:
            pd = dates[i - 1]
            daily_ret = sum(prev[s] * (px[s][d] / px[s][pd] - 1.0) for s in S)
            eq *= 1.0 + daily_ret

        rebalance = ((i - start) % REBALANCE_EVERY == 0)
        if rebalance:
            if mode == "strategy":
                tgt = target(i)
            elif mode == "equal_weight":
                tgt = {s: 1.0 / len(S) for s in S}
            elif mode == "btc":
                tgt = {s: (1.0 if s == "BTCUSDT" else 0.0) for s in S}
            else:
                raise ValueError(mode)
        else:
            tgt = prev.copy()

        delta = sum(abs(tgt[s] - prev[s]) for s in S)
        turnover += delta / 2.0
        cost = (FEE + SLIP) * cost_mult * delta
        eq *= 1.0 - cost
        cost_sum += cost
        prev = tgt
        curve.append(eq)

    liquidation = sum(abs(v) for v in prev.values())
    liq_cost = (FEE + SLIP) * cost_mult * liquidation
    eq *= 1.0 - liq_cost
    cost_sum += liq_cost
    curve[-1] = eq

    return {
        "metrics": metrics(curve),
        "one_way_turnover": turnover,
        "mean_daily_one_way_turnover": turnover / len(curve),
        "transaction_cost_fraction": cost_sum,
        "funding_pnl_sum": funding_sum,
        "funding_events": funding_events
    }

n = len(oos)
mid = split + n // 2
base = simulate(split, len(dates), "strategy", 1.0, True)
benchmark_equal = simulate(split, len(dates), "equal_weight", 1.0, True)
benchmark_btc = simulate(split, len(dates), "btc", 1.0, True)
first_half = simulate(split, mid, "strategy", 1.0, True)
second_half = simulate(mid, len(dates), "strategy", 1.0, True)
cost_stress = {
    f"{mult:.1f}x": simulate(split, len(dates), "strategy", mult, True)["metrics"]
    for mult in (1.0, 1.5, 2.0)
}

manifest_payload = json.dumps(
    [{"path":p,"sha256":h,"bytes":z} for p,h,z in sorted(cache_hashes)],
    indent=2, sort_keys=True
) + "\n"
cache_manifest_sha256 = hashlib.sha256(manifest_payload.encode()).hexdigest()

result = {
    "experiment_id":"HARMONY-FIN-0012",
    "strategy_id":"cross_sectional_residual_momentum_20d_beta60_weekly_v1",
    "data":{
        "cache_key":"harmony-binance-um-2021-01-2025-10-v4",
        "cache_manifest_sha256":cache_manifest_sha256,
        "cache_file_count":len(cache_hashes),
        "panel_rows":len(dates),
        "panel_start":dates[0],
        "panel_end":dates[-1],
        "oos_rows":len(oos),
        "oos_start":oos[0],
        "oos_end":oos[-1],
        "symbols":S
    },
    "configuration":{
        "residual_horizon_days":RETURN_HORIZON,
        "beta_window_days":BETA_WINDOW,
        "signal":"asset_20d_return_minus_beta60_btc_20d_return",
        "signal_cutoff":"t-1 close",
        "portfolio":"long top 2 +0.25 each; short bottom 2 -0.25 each",
        "beta":"population_covariance_over_population_btc_variance",
        "rebalance_every_oos_observations":REBALANCE_EVERY,
        "fee_rate":FEE,
        "slippage_rate":SLIP,
        "funding":"native settlement timestamps and native intervals",
        "terminal_liquidation":True,
        "parameters_fit_on_oos":False,
        "no_parameter_grid":True,
        "no_post_hoc_parameter_search":True
    },
    "oos_strategy":base,
    "benchmarks":{
        "same_universe_equal_weight_long_only":benchmark_equal,
        "BTCUSDT_buy_and_hold":benchmark_btc
    },
    "temporal_stability":{"first_half":first_half,"second_half":second_half},
    "cost_stress":cost_stress,
    "integrity":{
        "fixed_universe":True,
        "prior_only_price_signal":True,
        "native_funding_timestamps_used":True,
        "no_forward_fill":True,
        "no_universe_search":True,
        "no_post_convergence_optimization":True,
        "prior_experiments_modified":False,
        "verified_cache_reused":True
    }
}

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "cache_manifest.json").write_text(manifest_payload)
actual_manifest_sha256 = hashlib.sha256((OUT / "cache_manifest.json").read_bytes()).hexdigest()
if actual_manifest_sha256 != cache_manifest_sha256:
    raise RuntimeError(f"cache manifest self-hash mismatch: declared={cache_manifest_sha256} actual={actual_manifest_sha256}")
result["data"]["cache_manifest_sha256"] = actual_manifest_sha256
result["integrity"]["cache_manifest_bound_to_result_bytes"] = True
(OUT / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
print(json.dumps(result, indent=2, sort_keys=True))

# --- v2 reproducibility/durability diagnostics ---

from pathlib import Path

DUR_OUT = Path("artifacts/HARMONY-FIN-0012-DURABILITY-V2")
DUR_OUT.mkdir(parents=True, exist_ok=True)

def safe_metrics(returns):
    eq = [1.0]
    for r in returns:
        eq.append(eq[-1] * (1.0 + r))
    rr = returns
    sd = statistics.stdev(rr) if len(rr) > 1 else 0.0
    sharpe = statistics.mean(rr) / sd * math.sqrt(365.25) if sd else 0.0
    peak = eq[0]
    mdd = 0.0
    for x in eq:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1.0) if peak != 0 else mdd
    cagr = None if eq[-1] <= 0 else eq[-1] ** (365.25 / max(1, len(eq) - 1)) - 1.0
    return {
        "cumulative_return": eq[-1] - 1.0,
        "cagr": cagr,
        "sharpe": sharpe,
        "max_drawdown": mdd,
        "final_equity": eq[-1],
        "observations": len(eq),
    }

def diagnostic_metrics(returns):
    eq = [1.0]
    for r in returns:
        eq.append(eq[-1] * (1.0 + r))
    sd = statistics.stdev(returns) if len(returns) > 1 else 0.0
    sharpe = statistics.mean(returns) / sd * math.sqrt(365.25) if sd else 0.0
    downside = [min(0.0, r) for r in returns]
    dsd = statistics.stdev(downside) if len(downside) > 1 else 0.0
    sortino = statistics.mean(returns) / dsd * math.sqrt(365.25) if dsd else 0.0
    peak = eq[0]
    mdd = 0.0
    for x in eq:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1.0) if peak else mdd
    cagr = None if eq[-1] <= 0 else eq[-1] ** (365.25 / max(1, len(returns))) - 1.0
    return {
        "cumulative_return": eq[-1] - 1.0,
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": mdd,
        "final_equity": eq[-1],
        "observations": len(returns),
    }

WINDOW = 60

ACCEPTED = {
    "cumulative_return": 0.4169009579470373,
    "cagr": 0.273179006712682,
    "sharpe": 0.9706848261971941,
    "max_drawdown": -0.2791530147083816,
    "turnover": 37.25,
    "transaction_costs": 0.08304999999999994,
    "funding_pnl": 0.005558342500000007,
}

# The original engine has already loaded px/funding/dates and computed base.
gate = {
    "cumulative_return": (base["metrics"]["cumulative_return"], ACCEPTED["cumulative_return"]),
    "cagr": (base["metrics"]["cagr"], ACCEPTED["cagr"]),
    "sharpe": (base["metrics"]["sharpe"], ACCEPTED["sharpe"]),
    "max_drawdown": (base["metrics"]["max_drawdown"], ACCEPTED["max_drawdown"]),
    "turnover": (base["one_way_turnover"], ACCEPTED["turnover"]),
    "transaction_costs": (base["transaction_cost_fraction"], ACCEPTED["transaction_costs"]),
    "funding_pnl": (base["funding_pnl_sum"], ACCEPTED["funding_pnl"]),
}
gate_mismatches = {
    k: {"observed": o, "accepted": e}
    for k, (o, e) in gate.items()
    if abs(o - e) > 1e-9
}
if gate_mismatches:
    raise RuntimeError("VERBATIM REPRODUCTION GATE FAILED: " + json.dumps(gate_mismatches, sort_keys=True))

split = math.floor(len(dates) * 0.70)

def trace_simulate(start, end, mode, cost_mult=1.0):
    eq = 1.0
    prev = {s: 0.0 for s in S}
    curve = []
    turnover = 0.0
    cost_sum = 0.0
    funding_sum = 0.0

    for i in range(start, end):
        d = dates[i]

        for s in S:
            for interval, rate in funding[s].get(d, []):
                eq *= 1.0 - prev[s] * rate
                funding_sum += -prev[s] * rate

        if i > start:
            pd = dates[i - 1]
            daily_ret = sum(prev[s] * (px[s][d] / px[s][pd] - 1.0) for s in S)
            eq *= 1.0 + daily_ret

        rebalance = ((i - start) % REBALANCE_EVERY == 0)
        if rebalance:
            if mode == "strategy":
                tgt = target(i)
            elif mode == "equal_weight":
                tgt = {s: 1.0 / len(S) for s in S}
            elif mode == "btc":
                tgt = {s: (1.0 if s == "BTCUSDT" else 0.0) for s in S}
            else:
                raise ValueError(mode)
        else:
            tgt = prev.copy()

        delta = sum(abs(tgt[s] - prev[s]) for s in S)
        turnover += delta / 2.0
        cost = (FEE + SLIP) * cost_mult * delta
        eq *= 1.0 - cost
        cost_sum += cost
        prev = tgt
        curve.append(eq)

    liquidation = sum(abs(v) for v in prev.values())
    liq_cost = (FEE + SLIP) * cost_mult * liquidation
    eq *= 1.0 - liq_cost
    cost_sum += liq_cost
    curve[-1] = eq

    trace_metrics = metrics(curve)
    if mode == "strategy" and abs(cost_mult - 1.0) < 1e-12 and abs(trace_metrics["cumulative_return"] - base["metrics"]["cumulative_return"]) > 1e-9:
        raise RuntimeError("TRACE REPRODUCTION GATE FAILED: " + json.dumps({
            "trace": trace_metrics,
            "authoritative": base["metrics"],
        }, sort_keys=True))

    return {
        "curve": curve,
        "metrics": trace_metrics,
        "turnover": turnover,
        "transaction_cost_fraction": cost_sum,
        "funding_pnl_sum": funding_sum,
    }

trace_base = trace_simulate(split, len(dates), "strategy", 1.0)
trace_authoritative = {
    "cumulative_return": trace_base["metrics"]["cumulative_return"],
    "cagr": trace_base["metrics"]["cagr"],
    "sharpe": trace_base["metrics"]["sharpe"],
    "max_drawdown": trace_base["metrics"]["max_drawdown"],
    "turnover": trace_base["turnover"],
    "transaction_costs": trace_base["transaction_cost_fraction"],
    "funding_pnl": trace_base["funding_pnl_sum"],
}
trace_full_mismatches = {
    k: {"trace": trace_authoritative[k], "accepted": ACCEPTED[k]}
    for k in ACCEPTED
    if abs(trace_authoritative[k] - ACCEPTED[k]) > 1e-9
}
if trace_full_mismatches:
    raise RuntimeError("TRACE REPRODUCTION GATE FAILED: " + json.dumps(trace_full_mismatches, sort_keys=True))

trace_btc = trace_simulate(split, len(dates), "btc", 1.0)
trace_ew = trace_simulate(split, len(dates), "equal_weight", 1.0)

def point_returns(curve):
    return [curve[i] / curve[i-1] - 1.0 for i in range(1, len(curve))]

strategy_returns = [trace_base["curve"][0] - 1.0] + point_returns(trace_base["curve"])
btc_returns = [trace_btc["curve"][0] - 1.0] + point_returns(trace_btc["curve"])
ew_returns = [trace_ew["curve"][0] - 1.0] + point_returns(trace_ew["curve"])

diagnostic_global = metrics(trace_base["curve"])
diagnostic_gate = {
    "cumulative_return": (diagnostic_global["cumulative_return"], ACCEPTED["cumulative_return"]),
    "cagr": (diagnostic_global["cagr"], ACCEPTED["cagr"]),
    "sharpe": (diagnostic_global["sharpe"], ACCEPTED["sharpe"]),
    "max_drawdown": (diagnostic_global["max_drawdown"], ACCEPTED["max_drawdown"]),
}
oos_dates = dates[split:]

def factor_residual(strategy, btc, ew, window=60):
    residual = [None] * len(strategy)
    betas = [None] * len(strategy)
    r2s = [None] * len(strategy)
    for i in range(window, len(strategy)):
        y = strategy[i-window:i]
        x = btc[i-window:i]
        z = ew[i-window:i]
        mx, mz, my = statistics.mean(x), statistics.mean(z), statistics.mean(y)
        s11 = sum((v-mx)**2 for v in x)
        s22 = sum((v-mz)**2 for v in z)
        s12 = sum((a-mx)*(b-mz) for a,b in zip(x,z))
        sy1 = sum((a-mx)*(b-my) for a,b in zip(x,y))
        sy2 = sum((a-mz)*(b-my) for a,b in zip(x,y))
        det = s11*s22 - s12*s12
        if abs(det) < 1e-18:
            b1 = b2 = 0.0
        else:
            b1 = (sy1*s22 - sy2*s12)/det
            b2 = (sy2*s11 - sy1*s12)/det
        intercept = my - b1*mx - b2*mz
        residual[i] = strategy[i] - (intercept + b1*strategy[i]*0 + b1*btc[i] + b2*ew[i])
        betas[i] = (b1,b2)
        total = sum((v-my)**2 for v in y)
        error = sum((v-(intercept+b1*u+b2*v2))**2 for v,u,v2 in zip(y,x,z))
        r2s[i] = 1.0 - error/total if total else 0.0
    return residual, betas, r2s

residual, betas, r2s = factor_residual(strategy_returns, btc_returns, ew_returns)

def segment_rows():
    n = len(strategy_returns)
    q = n // 4
    specs = [
        ("half1", 0, n//2), ("half2", n//2, n),
        ("quarter1",0,q), ("quarter2",q,2*q),
        ("quarter3",2*q,3*q), ("quarter4",3*q,n)
    ]
    out=[]
    for name,a,b in specs:
        rr = strategy_returns[a:b]
        res = [x for x in residual[a:b] if x is not None]
        out.append({
            "segment":name,
            "start":oos_dates[a],
            "end":oos_dates[b-1],
            "observations":b-a,
            "cumulative_return": diagnostic_metrics(rr)["cumulative_return"],
            "cagr": diagnostic_metrics(rr)["cagr"],
            "sharpe": diagnostic_metrics(rr)["sharpe"],
            "sortino": diagnostic_metrics(rr)["sortino"],
            "max_drawdown": diagnostic_metrics(rr)["max_drawdown"],
            "residual_cumulative_return": "" if not res else safe_metrics(res)["cumulative_return"],
            "residual_sharpe": "" if not res else safe_metrics(res)["sharpe"],
        })
    return out

segments = segment_rows()

def rolling(window):
    out=[]
    for i in range(window,len(strategy_returns)+1):
        mm=diagnostic_metrics(strategy_returns[i-window:i])
        out.append({
            "end":oos_dates[i-1],
            "cumulative_return":mm["cumulative_return"],
            "cagr":mm["cagr"],
            "sharpe":mm["sharpe"],
            "max_drawdown":mm["max_drawdown"],
        })
    return out

roll90=rolling(90)
roll180=rolling(180)

factor_quarters=[]
n=len(strategy_returns); q=n//4
for k in range(4):
    a=k*q; b=(k+1)*q if k<3 else n
    res=[x for x in residual[a:b] if x is not None]
    if res:
        mm=safe_metrics(res)
        factor_quarters.append({"quarter":k+1,"sharpe":mm["sharpe"],"sortino":mm.get("sortino"),"cumulative_return":mm["cumulative_return"],"max_drawdown":mm["max_drawdown"]})

cost_stress={}
for mult in (1.0,1.5,2.0):
    tr=trace_simulate(split,len(dates),"strategy",mult)
    cost_stress[f"{mult:.1f}x"]={"raw":tr["metrics"]}

by_year={}
by_quarter={}
for d,r in zip(oos_dates,strategy_returns):
    by_year.setdefault(d[:4],[]).append(r)
    month=int(d[5:7])
    qkey=f"{d[:4]}-Q{((month-1)//3)+1}"
    by_quarter.setdefault(qkey,[]).append(r)
year_metrics={k:diagnostic_metrics(v) for k,v in by_year.items()}
quarter_metrics={k:diagnostic_metrics(v) for k,v in by_quarter.items()}
total=base["metrics"]["cumulative_return"]
best_year=max(year_metrics.items(),key=lambda kv:kv[1]["cumulative_return"])
best_quarter=max(quarter_metrics.items(),key=lambda kv:kv[1]["cumulative_return"])

half1 = segments[0]
half2 = segments[1]
residual_full = [x for x in residual[WINDOW:] if x is not None]
residual_full_metrics = safe_metrics(residual_full) if residual_full else {}
two_x_sharpe = cost_stress["2.0x"]["raw"]["sharpe"]
all_residual_quarter_positive = all(x["sharpe"] > 0 for x in factor_quarters if x.get("sharpe") is not None)
if half1["sharpe"] > 0 and half2["sharpe"] > 0 and all_residual_quarter_positive and two_x_sharpe > 0:
    durability_status = "DURABILITY_SUPPORTED"
elif residual_full_metrics.get("sharpe", 0.0) > 0:
    durability_status = "DURABILITY_MIXED"
else:
    durability_status = "DURABILITY_NOT_SUPPORTED"

manifest_files = []
for s in S:
    for y,m in months():
        for p in (
            ROOT/"klines"/s/"1d"/f"{s}-1d-{y:04d}-{m:02d}.zip",
            ROOT/"fundingRate"/s/f"{s}-fundingRate-{y:04d}-{m:02d}.zip",
        ):
            raw=p.read_bytes()
            manifest_files.append({"path":str(p),"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()})
def json_sanitize(value, counter=None):
    if counter is None:
        counter = {"complex": 0}
    if isinstance(value, complex):
        counter["complex"] += 1
        return None
    if isinstance(value, dict):
        return {k: json_sanitize(v, counter) for k, v in value.items()}
    if isinstance(value, list):
        return [json_sanitize(v, counter) for v in value]
    if isinstance(value, tuple):
        return [json_sanitize(v, counter) for v in value]
    return value

manifest = {
    "engine_source_commit":"8388dc680571bf3fa849d349b60de99e38ebf8ea",
    "cache_key":"harmony-binance-um-2021-01-2025-10-v4",
    "files":manifest_files,
    "holdout_access":False,
    "candidate_mutation":False,
    "parameter_search":False,
}
manifest_bytes=json.dumps(manifest,sort_keys=True,indent=2).encode()+b"\\n"
(DUR_OUT/"input_manifest.json").write_bytes(manifest_bytes)

payload={
    "version":"2.0",
    "status":durability_status,
    "diagnostic_global":diagnostic_global,
    "diagnostic_gate":diagnostic_gate,
    "reproduction_gate":gate,
    "trace_metrics":trace_base["metrics"],
    "temporal_segments":segments,
    "factor_quarters":factor_quarters,
    "rolling_90":{
        "median_sharpe":statistics.median(x["sharpe"] for x in roll90),
        "q25_sharpe":sorted(x["sharpe"] for x in roll90)[len(roll90)//4],
        "q75_sharpe":sorted(x["sharpe"] for x in roll90)[3*len(roll90)//4],
        "positive_return_fraction":sum(x["cumulative_return"]>0 for x in roll90)/len(roll90),
        "positive_sharpe_fraction":sum(x["sharpe"]>0 for x in roll90)/len(roll90),
    },
    "rolling_180":{
        "median_sharpe":statistics.median(x["sharpe"] for x in roll180),
        "q25_sharpe":sorted(x["sharpe"] for x in roll180)[len(roll180)//4],
        "q75_sharpe":sorted(x["sharpe"] for x in roll180)[3*len(roll180)//4],
        "positive_return_fraction":sum(x["cumulative_return"]>0 for x in roll180)/len(roll180),
        "positive_sharpe_fraction":sum(x["sharpe"]>0 for x in roll180)/len(roll180),
    },
    "cost_stress":cost_stress,
    "timing":{
        "by_year":year_metrics,
        "by_quarter":quarter_metrics,
        "best_year":best_year[0],
        "best_year_contribution_fraction":best_year[1]["cumulative_return"]/total if total>0 else None,
        "best_quarter":best_quarter[0],
        "best_quarter_contribution_fraction":best_quarter[1]["cumulative_return"]/total if total>0 else None,
    },
    "integrity":{
        "candidate_immutable":True,
        "holdout_access":False,
        "parameter_search":False,
        "universe_search":False,
    },
}
complex_counter = {"complex": 0}
safe_payload = json_sanitize(payload, complex_counter)
safe_payload["serialization"] = {
    "complex_values_sanitized": complex_counter["complex"],
    "undefined_residual_cagr_encoded_as_null": True,
}
(DUR_OUT/"durability_audit.json").write_text(
    json.dumps(safe_payload, sort_keys=True, indent=2) + "\n"
)

with (DUR_OUT/"oos_segments.csv").open("w",newline="") as f:
    w=csv.DictWriter(f,fieldnames=list(segments[0])); w.writeheader(); w.writerows(segments)
with (DUR_OUT/"rolling_90.csv").open("w",newline="") as f:
    w=csv.DictWriter(f,fieldnames=list(roll90[0])); w.writeheader(); w.writerows(roll90)
with (DUR_OUT/"rolling_180.csv").open("w",newline="") as f:
    w=csv.DictWriter(f,fieldnames=list(roll180[0])); w.writeheader(); w.writerows(roll180)
with (DUR_OUT/"equity_and_residual.csv").open("w",newline="") as f:
    fields=["date","strategy_equity","strategy_return","residual_return","residual_equity","rolling_btc_beta","rolling_equal_weight_beta","rolling_r2"]
    w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
    re=1.0
    for i,d in enumerate(oos_dates):
        if residual[i] is not None:
            re*=1.0+residual[i]
        w.writerow({
            "date":d,
            "strategy_equity":trace_base["curve"][i],
            "strategy_return":strategy_returns[i],
            "residual_return":"" if residual[i] is None else residual[i],
            "residual_equity":"" if residual[i] is None else re,
            "rolling_btc_beta":"" if betas[i] is None else betas[i][0],
            "rolling_equal_weight_beta":"" if betas[i] is None else betas[i][1],
            "rolling_r2":"" if r2s[i] is None else r2s[i],
        })
(DUR_OUT/"cost_stress.json").write_text(json.dumps(cost_stress,sort_keys=True,indent=2)+"\\n")

report=[
    "# FIN-0012 Reproducibility-First Durability Audit v2",
    "",
    "The accepted FIN-0012 engine was executed verbatim from commit 8388dc680571bf3fa849d349b60de99e38ebf8ea.",
    "No candidate mutation, parameter search, universe search, or holdout access was used.",
    "",
    f"## Research status: {durability_status}",
    "",
    "## Reproduction gate",
    json.dumps(gate,indent=2,sort_keys=True),
    "",
    "## Trace match",
    json.dumps(trace_base["metrics"],indent=2,sort_keys=True),
    "",
    "## Temporal durability",
    json.dumps(json_sanitize(segments),indent=2,sort_keys=True),
    "",
    "## Factor-residual durability",
    json.dumps(json_sanitize(factor_quarters),indent=2,sort_keys=True),
    "",
    "## Rolling durability",
    json.dumps({"90":payload["rolling_90"],"180":payload["rolling_180"]},indent=2,sort_keys=True),
    "",
    "## Cost stress",
    json.dumps(json_sanitize(cost_stress),indent=2,sort_keys=True),
    "",
    "## Timing concentration",
    json.dumps(json_sanitize(payload["timing"]),indent=2,sort_keys=True),
]
(DUR_OUT/"durability_report.md").write_text("\\n".join(report)+"\\n")


# --- Authoritative v2 diagnostic finalization; executed only after full reproduction gates above ---

def metrics_from_curve_v2(curve):
    mm = metrics(curve)
    rr = point_returns(curve)
    downside = [min(r, 0.0) ** 2 for r in rr]
    dd = math.sqrt(sum(downside) / len(rr)) if rr else 0.0
    mm["sortino"] = (statistics.mean(rr) / dd) * math.sqrt(365.25) if dd else 0.0
    return mm

def metrics_from_returns_v2(rr):
    curve = [1.0]
    for r in rr:
        curve.append(curve[-1] * (1.0 + r))
    return metrics_from_curve_v2(curve)

def normalize_curve(curve):
    if not curve:
        raise RuntimeError("empty curve")
    base_eq = curve[0]
    return [x / base_eq for x in curve]

# Use the complete 528-observation OOS trace for temporal, rolling, and timing durability.
oos_curve = trace_base["curve"]
oos_dates = dates[split:]
assert len(oos_curve) == 528 and len(oos_dates) == 528

def temporal_segment(name, a, b):
    curve = normalize_curve(oos_curve[a:b])
    mm = metrics_from_curve_v2(curve)
    return {
        "segment": name,
        "start": oos_dates[a],
        "end": oos_dates[b-1],
        "observations": b-a,
        "cumulative_return": mm["cumulative_return"],
        "cagr": mm["cagr"],
        "sharpe": mm["sharpe"],
        "sortino": mm["sortino"],
        "max_drawdown": mm["max_drawdown"],
    }

half = len(oos_curve) // 2
q = len(oos_curve) // 4
segments_v2 = [
    temporal_segment("first_half", 0, half),
    temporal_segment("second_half", half, len(oos_curve)),
    temporal_segment("quarter1", 0, q),
    temporal_segment("quarter2", q, 2*q),
    temporal_segment("quarter3", 2*q, 3*q),
    temporal_segment("quarter4", 3*q, len(oos_curve)),
]

def rolling_v2(window):
    out = []
    for i in range(window, len(oos_curve)+1):
        curve = normalize_curve(oos_curve[i-window:i])
        mm = metrics_from_curve_v2(curve)
        out.append({
            "start": oos_dates[i-window],
            "end": oos_dates[i-1],
            "observations": window,
            "cumulative_return": mm["cumulative_return"],
            "cagr": mm["cagr"],
            "sharpe": mm["sharpe"],
            "sortino": mm["sortino"],
            "max_drawdown": mm["max_drawdown"],
        })
    return out

roll90_v2 = rolling_v2(90)
roll180_v2 = rolling_v2(180)

def rolling_summary_v2(items):
    sh = [x["sharpe"] for x in items]
    return {
        "median_sharpe": statistics.median(sh),
        "q25_sharpe": statistics.quantiles(sh, n=4, method="inclusive")[0],
        "q75_sharpe": statistics.quantiles(sh, n=4, method="inclusive")[2],
        "positive_return_fraction": sum(x["cumulative_return"] > 0 for x in items) / len(items),
        "positive_sharpe_fraction": sum(x["sharpe"] > 0 for x in items) / len(items),
        "min_sharpe": min(sh),
        "max_sharpe": max(sh),
        "window_count": len(items),
    }

roll90_summary_v2 = rolling_summary_v2(roll90_v2)
roll180_summary_v2 = rolling_summary_v2(roll180_v2)

# Factor-residual attribution uses true inter-day return intervals, excluding the
# first OOS observation because it has no preceding day return.
strategy_daily = point_returns(trace_base["curve"])
btc_daily = point_returns(trace_btc["curve"])
ew_daily = point_returns(trace_ew["curve"])
daily_dates = dates[split+1:]
residual, betas, r2s = factor_residual(strategy_daily, btc_daily, ew_daily)

residual_clean = [r for r in residual if r is not None]
residual_full_v2 = metrics_from_returns_v2(residual_clean)

factor_quarters_v2 = []
for k in range(4):
    a_obs = k*q
    b_obs = (k+1)*q if k < 3 else len(oos_dates)
    # Map OOS observation boundaries to inter-day return boundaries.
    a = max(0, a_obs-1)
    b = min(len(strategy_daily), max(0, b_obs-1))
    clean = [r for r in residual[a:b] if r is not None]
    rm = metrics_from_returns_v2(clean) if clean else None
    factor_quarters_v2.append({
        "quarter": k+1,
        "start": oos_dates[a_obs],
        "end": oos_dates[b_obs-1],
        "observations": len(clean),
        "cumulative_return": None if rm is None else rm["cumulative_return"],
        "cagr": None if rm is None else rm["cagr"],
        "sharpe": None if rm is None else rm["sharpe"],
        "sortino": None if rm is None else rm["sortino"],
        "max_drawdown": None if rm is None else rm["max_drawdown"],
    })

def residual_metrics_for_trace_v2(trace):
    rr = point_returns(trace["curve"])
    b = btc_daily
    z = ew_daily
    res, _, _ = factor_residual(rr, b, z)
    clean = [r for r in res if r is not None]
    return metrics_from_returns_v2(clean) if clean else None

cost_stress_v2 = {}
for mult in (1.0, 1.5, 2.0):
    tr = trace_simulate(split, len(dates), "strategy", mult)
    cost_stress_v2[f"{mult:.1f}x"] = {
        "raw": metrics_from_curve_v2(tr["curve"]),
        "one_way_turnover": tr["turnover"],
        "transaction_cost_fraction": tr["transaction_cost_fraction"],
        "funding_pnl_sum": tr["funding_pnl_sum"],
        "residual": residual_metrics_for_trace_v2(tr),
    }

def period_group_indices(key_fn):
    groups = {}
    for i, d in enumerate(oos_dates):
        groups.setdefault(key_fn(d), []).append(i)
    return groups

def timing_metrics_from_indices(indices):
    a, b = indices[0], indices[-1]+1
    curve = normalize_curve(oos_curve[a:b])
    mm = metrics_from_curve_v2(curve)
    return {
        "observations": len(indices),
        "start": oos_dates[a],
        "end": oos_dates[b-1],
        "cumulative_return": mm["cumulative_return"],
        "cagr": mm["cagr"],
        "sharpe": mm["sharpe"],
        "sortino": mm["sortino"],
        "max_drawdown": mm["max_drawdown"],
    }

year_groups = period_group_indices(lambda d: d[:4])
quarter_groups = period_group_indices(lambda d: f"{d[:4]}-Q{((int(d[5:7])-1)//3)+1}")
by_year_v2 = {k: timing_metrics_from_indices(v) for k,v in year_groups.items()}
by_quarter_v2 = {k: timing_metrics_from_indices(v) for k,v in quarter_groups.items()}

total_profit_v2 = base["metrics"]["cumulative_return"]
best_year_v2 = max(by_year_v2, key=lambda k: by_year_v2[k]["cumulative_return"])
best_quarter_v2 = max(by_quarter_v2, key=lambda k: by_quarter_v2[k]["cumulative_return"])
timing_v2 = {
    "by_year": by_year_v2,
    "by_quarter": by_quarter_v2,
    "best_year": best_year_v2,
    "best_year_contribution_fraction_of_total_profit": by_year_v2[best_year_v2]["cumulative_return"]/total_profit_v2,
    "best_quarter": best_quarter_v2,
    "best_quarter_contribution_fraction_of_total_profit": by_quarter_v2[best_quarter_v2]["cumulative_return"]/total_profit_v2,
}

half_v2 = segments_v2[:2]
quarter_v2 = segments_v2[2:]
temporal_distributed_v2 = (
    all(x["cumulative_return"] > 0 and x["sharpe"] > 0 for x in half_v2)
    and sum(x["cumulative_return"] > 0 for x in quarter_v2) >= 3
)
residual_persistent_v2 = (
    residual_full_v2 is not None
    and residual_full_v2["cumulative_return"] > 0
    and sum((x["cumulative_return"] or 0) > 0 for x in factor_quarters_v2) >= 3
)
raw_2x_v2 = cost_stress_v2["2.0x"]["raw"]
res_2x_v2 = cost_stress_v2["2.0x"]["residual"]
cost_preserved_v2 = (
    raw_2x_v2["cumulative_return"] > 0
    and res_2x_v2 is not None
    and res_2x_v2["cumulative_return"] > 0
)
best_year_frac_v2 = timing_v2["best_year_contribution_fraction_of_total_profit"]
best_quarter_frac_v2 = timing_v2["best_quarter_contribution_fraction_of_total_profit"]
not_concentrated_v2 = (
    best_year_frac_v2 <= 0.75 and best_quarter_frac_v2 <= 0.50
)

if temporal_distributed_v2 and residual_persistent_v2 and cost_preserved_v2 and not_concentrated_v2:
    final_status_v2 = "DURABILITY_SUPPORTED"
elif (
    sum(x["cumulative_return"] > 0 for x in half_v2) == 0
    or (residual_full_v2 is not None and residual_full_v2["cumulative_return"] <= 0 and raw_2x_v2["cumulative_return"] <= 0)
    or best_quarter_frac_v2 > 0.85
):
    final_status_v2 = "DURABILITY_NOT_SUPPORTED"
else:
    final_status_v2 = "DURABILITY_MIXED"

final_payload = {
    "version": "2.0",
    "status": final_status_v2,
    "reproduction_gate": {"all_within_1e-9": True, "values": gate},
    "trace_gate": {"all_within_1e-9": True, "values": trace_authoritative},
    "panel": {
        "common_rows": len(dates),
        "oos_rows": len(oos),
        "oos_start": oos[0],
        "oos_end": oos[-1],
        "cache_key": "harmony-binance-um-2021-01-2025-10-v4",
    },
    "temporal": {
        "segments": segments_v2,
        "temporally_distributed": temporal_distributed_v2,
    },
    "rolling_90": roll90_summary_v2,
    "rolling_180": roll180_summary_v2,
    "factor_residual": {
        "definition": "strategy return = intercept + BTC return beta + equal-weight benchmark beta + residual",
        "note": "Residual return is a diagnostic factor residual and is not causal alpha.",
        "full_period": residual_full_v2,
        "by_quarter": factor_quarters_v2,
        "persists": residual_persistent_v2,
    },
    "cost_stress": cost_stress_v2,
    "cost_stress_preserves": cost_preserved_v2,
    "timing": timing_v2,
    "classification_facts": {
        "temporal_distributed": temporal_distributed_v2,
        "residual_persistent": residual_persistent_v2,
        "cost_preserved": cost_preserved_v2,
        "not_extremely_concentrated": not_concentrated_v2,
    },
    "next_stage": {
        "separate_frozen_factor_neutral_descendant_justified": bool(residual_persistent_v2 and cost_preserved_v2),
        "created": False,
    },
    "integrity": {
        "candidate_immutable": True,
        "holdout_access": False,
        "parameter_search": False,
        "universe_search": False,
        "direction_search": False,
        "failed_v1_outputs_reused": False,
    },
}
DUR_OUT.mkdir(parents=True, exist_ok=True)
(DUR_OUT / "durability_audit.json").write_text(json.dumps(final_payload, sort_keys=True, indent=2) + "\n")
(DUR_OUT / "cost_stress.json").write_text(json.dumps(cost_stress_v2, sort_keys=True, indent=2) + "\n")

with (DUR_OUT / "oos_segments.csv").open("w", newline="") as f:
    fields = list(segments_v2[0].keys())
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(segments_v2)

with (DUR_OUT / "rolling_90.csv").open("w", newline="") as f:
    fields = list(roll90_v2[0].keys())
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(roll90_v2)

with (DUR_OUT / "rolling_180.csv").open("w", newline="") as f:
    fields = list(roll180_v2[0].keys())
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(roll180_v2)

rf_residual, rf_betas, rf_r2s = factor_residual(strategy_daily, btc_daily, ew_daily)
with (DUR_OUT / "equity_and_residual.csv").open("w", newline="") as f:
    fields = [
        "date","strategy_equity","strategy_return","residual_return",
        "residual_equity","rolling_btc_beta","rolling_equal_weight_beta","rolling_r2"
    ]
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    residual_equity = 1.0
    # First row has no inter-day return attribution; it is retained as a blank residual row.
    for i, d in enumerate(oos_dates):
        if i == 0:
            w.writerow({
                "date": d,
                "strategy_equity": oos_curve[i],
                "strategy_return": oos_curve[i] - 1.0,
                "residual_return": "",
                "residual_equity": "",
                "rolling_btc_beta": "",
                "rolling_equal_weight_beta": "",
                "rolling_r2": "",
            })
            continue
        j = i - 1
        if rf_residual[j] is not None:
            residual_equity *= 1.0 + rf_residual[j]
        w.writerow({
            "date": d,
            "strategy_equity": oos_curve[i],
            "strategy_return": strategy_daily[j],
            "residual_return": "" if rf_residual[j] is None else rf_residual[j],
            "residual_equity": "" if rf_residual[j] is None else residual_equity,
            "rolling_btc_beta": "" if rf_betas[j] is None else rf_betas[j][0],
            "rolling_equal_weight_beta": "" if rf_betas[j] is None else rf_betas[j][1],
            "rolling_r2": "" if rf_r2s[j] is None else rf_r2s[j],
        })

manifest_final = {
    "engine_source_commit": "8388dc680571bf3fa849d349b60de99e38ebf8ea",
    "engine_source_path": ".github/workflows/harmony-fin-0012.yml",
    "cache_key": "harmony-binance-um-2021-01-2025-10-v4",
    "files": [{"path": p, "sha256": h, "bytes": z} for p,h,z in sorted(cache_hashes)],
    "panel_rows": len(dates),
    "oos_rows": len(oos),
    "oos_start": oos[0],
    "oos_end": oos[-1],
    "holdout_access": False,
    "candidate_mutation": False,
    "parameter_search": False,
    "universe_search": False,
    "direction_search": False,
    "failed_v1_outputs_reused": False,
}
(DUR_OUT / "input_manifest.json").write_text(json.dumps(manifest_final, sort_keys=True, indent=2) + "\n")

report = f"""# FIN-0012 Reproducibility-First Durability Audit v2

## Final status

{final_status_v2}

This is a research status, not a trading recommendation.

## 1. Exact accepted result reproduced

Yes. The verbatim FIN-0012 engine from accepted execution commit 8388dc680571bf3fa849d349b60de99e38ebf8ea reproduced every accepted reproduction-gate value within 1e-9.

{json.dumps(gate, indent=2, sort_keys=True)}

Panel verification: 1760 common rows; 528 OOS rows; OOS {oos[0]} through {oos[-1]}; verified cache key harmony-binance-um-2021-01-2025-10-v4.

## 2. Trace independently matched it

Yes. The trace copy of the same simulation logic independently matched the authoritative OOS metrics before durability diagnostics.

{json.dumps(trace_authoritative, indent=2, sort_keys=True)}

## 3. Whether the edge is temporally distributed

{str(temporal_distributed_v2).upper()}

{json.dumps(segments_v2, indent=2, sort_keys=True)}

## 4. Whether residual performance persists

{str(residual_persistent_v2).upper()}

The attribution is strategy return = intercept + BTC return beta + equal-weight benchmark beta + residual. Residual return is a diagnostic factor residual, not causal alpha.

Full-period residual:
{json.dumps(residual_full_v2, indent=2, sort_keys=True)}

Residual by quarter:
{json.dumps(factor_quarters_v2, indent=2, sort_keys=True)}

## 5. Whether cost stress preserves it

{str(cost_preserved_v2).upper()}

{json.dumps(cost_stress_v2, indent=2, sort_keys=True)}

## 6. Whether a separate frozen factor-neutral descendant is justified

{str(bool(residual_persistent_v2 and cost_preserved_v2)).upper()}

No descendant was created in this audit.

## Rolling durability

90-observation summary:
{json.dumps(roll90_summary_v2, indent=2, sort_keys=True)}

180-observation summary:
{json.dumps(roll180_summary_v2, indent=2, sort_keys=True)}

## Timing concentration

{json.dumps(timing_v2, indent=2, sort_keys=True)}

## Classification rule

DURABILITY_SUPPORTED requires both OOS halves positive on cumulative return and Sharpe, at least 3 of 4 quarters positive, positive full-period residual return and at least 3 of 4 residual quarters positive, positive raw and residual cumulative return at 2.0x costs, and no extreme concentration above 50 percent of total profit in the best quarter or 75 percent in the best year.

DURABILITY_NOT_SUPPORTED is reserved for clear failure patterns; all other outcomes are DURABILITY_MIXED.
"""
(DUR_OUT / "durability_report.md").write_text(report)
print(final_status_v2)
