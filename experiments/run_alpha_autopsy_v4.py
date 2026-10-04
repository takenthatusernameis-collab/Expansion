#!/usr/bin/env python3
"""Accounting-reconciled alpha autopsy for frozen FIN-0012 / FIN-0024."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import statistics
from pathlib import Path

from experiments import run_alpha_autopsy_v1 as v1

SYMBOLS = v1.SYMBOLS
OOS_START = v1.OOS_START
END = v1.END
FEE = v1.FEE
SLIP = v1.SLIP
WINDOW = v1.WINDOW
PLACEBOS = v1.PLACEBOS
SEED = v1.SEED
OUT = Path("artifacts/HARMONY-ALPHA-AUTOPSY-V2")

ACCEPTED_FIN12 = {
    "cumulative_return": 0.4169009579470373,
    "cagr": 0.273179006712682,
    "sharpe": 0.9706848261971941,
    "max_drawdown": -0.2791530147083816,
    "turnover": 37.25,
    "transaction_costs": 0.08304999999999994,
    "funding_pnl": 0.005558342500000007,
}

def point_returns(curve):
    return [curve[0] - 1.0] + [curve[i] / curve[i-1] - 1.0 for i in range(1, len(curve))]

def curve_metrics(curve):
    rr = point_returns(curve)[1:]
    sd = statistics.stdev(rr) if len(rr) > 1 else 0.0
    sharpe = statistics.mean(rr) / sd * math.sqrt(365.25) if sd else 0.0
    downside = [min(0.0, r) for r in rr]
    dsd = statistics.stdev(downside) if len(downside) > 1 else 0.0
    sortino = statistics.mean(rr) / dsd * math.sqrt(365.25) if dsd else 0.0
    peak = curve[0]
    mdd = 0.0
    for x in curve:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1.0) if peak else mdd
    cagr = curve[-1] ** (365.25 / max(1, len(curve) - 1)) - 1.0
    return {
        "cumulative_return": curve[-1] - 1.0,
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": mdd,
        "final_equity": curve[-1],
        "observations": len(curve),
    }

def simulate_exact(dates, close, funding, weight_fn, start_idx):
    rets = v1.daily_returns(close, dates)
    fr = v1.fund_map(funding, dates)
    prev = {s: 0.0 for s in SYMBOLS}
    gross = 1.0
    net = 1.0
    gross_curve = []
    net_curve = []
    turnover = 0.0
    costs = 0.0
    funding_pnl = 0.0

    for i in range(start_idx, len(dates)):
        d = dates[i]

        # Exact authoritative ordering: native funding settlement first.
        for s in SYMBOLS:
            for rate in funding[s].get(d, []):
                gross *= 1.0 - prev[s] * rate
                net *= 1.0 - prev[s] * rate
                funding_pnl += -prev[s] * rate

        if i > start_idx:
            daily_price = sum(prev[s] * rets[s].get(d, 0.0) for s in SYMBOLS)
            gross *= 1.0 + daily_price
            net *= 1.0 + daily_price

        target = weight_fn(i, d)
        if target is not None:
            delta = sum(abs(target.get(s, 0.0) - prev[s]) for s in SYMBOLS)
            cost = (FEE + SLIP) * delta
            net *= 1.0 - cost
            turnover += delta / 2.0
            costs += cost
            prev = {s: target.get(s, 0.0) for s in SYMBOLS}

        gross_curve.append(gross)
        net_curve.append(net)

    liquidation = sum(abs(v) for v in prev.values())
    liq_cost = (FEE + SLIP) * liquidation
    net *= 1.0 - liq_cost
    costs += liq_cost
    net_curve[-1] = net

    return {
        "gross_curve": gross_curve,
        "net_curve": net_curve,
        "gross_metrics": curve_metrics(gross_curve),
        "net_metrics": curve_metrics(net_curve),
        "gross_returns": point_returns(gross_curve),
        "net_returns": point_returns(net_curve),
        "turnover": turnover,
        "costs": costs,
        "funding_pnl": funding_pnl,
        "liquidation_cost": liq_cost,
    }

def placebo_net(dates, close, funding, start_idx, n_longs, n_shorts, gross_per_leg, weight_builder, seed):
    rng = random.Random(seed)
    results = []
    rets = v1.daily_returns(close, dates)
    fr = v1.fund_map(funding, dates)

    for _ in range(PLACEBOS):
        prev = {s: 0.0 for s in SYMBOLS}
        net = 1.0
        curve = []

        for i in range(start_idx, len(dates)):
            d = dates[i]

            for s in SYMBOLS:
                for rate in funding[s].get(d, []):
                    net *= 1.0 - prev[s] * rate

            if i > start_idx:
                daily_price = sum(prev[s] * rets[s].get(d, 0.0) for s in SYMBOLS)
                net *= 1.0 + daily_price

            if (i - start_idx) % 7 == 0:
                deterministic = weight_builder(i, d)
                if deterministic is not None:
                    pool = list(SYMBOLS)
                    rng.shuffle(pool)
                    longs = pool[:n_longs]
                    remaining = [s for s in pool if s not in longs]
                    shorts = remaining[:n_shorts]
                    target = {s: 0.0 for s in SYMBOLS}
                    for s in longs:
                        target[s] = gross_per_leg
                    for s in shorts:
                        target[s] = -gross_per_leg
                    delta = sum(abs(target[s] - prev[s]) for s in SYMBOLS)
                    net *= 1.0 - (FEE + SLIP) * delta
                    prev = target

            curve.append(net)

        net *= 1.0 - (FEE + SLIP) * sum(abs(v) for v in prev.values())
        curve[-1] = net
        mm = curve_metrics(curve)
        results.append({
            "cum": mm["cumulative_return"],
            "sharpe": mm["sharpe"],
            "mdd": mm["max_drawdown"],
        })
    return results

def percentile(actual, values):
    return 100.0 * sum(v <= actual for v in values) / len(values)

def process(name, close, funding, raw_weight_builder, n_longs, n_shorts, gross_per_leg):
    dates = sorted(set.intersection(*(set(close[s]) for s in SYMBOLS)))
    dates = [d for d in dates if d <= END]
    start_idx = next(i for i, d in enumerate(dates) if d >= OOS_START)

    def candidate_weight(i, d):
        if (i - start_idx) % 7 != 0:
            return None
        return raw_weight_builder(close if name == "FIN-0012" else funding, dates, i)

    def benchmark_weight(kind):
        if kind == "btc":
            return {s: (1.0 if s == "BTCUSDT" else 0.0) for s in SYMBOLS}
        return {s: 1.0 / len(SYMBOLS) for s in SYMBOLS}

    strat = simulate_exact(dates, close, funding, candidate_weight, start_idx)
    btc = simulate_exact(dates, close, funding,
                         lambda i, d: benchmark_weight("btc") if i == start_idx else None,
                         start_idx)
    ew = simulate_exact(dates, close, funding,
                        lambda i, d: benchmark_weight("ew") if i == start_idx else None,
                        start_idx)

    # All attribution is explicitly net-vs-net.
    residual, betas, r2s = v1.rolling_attribution(
        strat["net_returns"], btc["net_returns"], ew["net_returns"]
    )
    usable_residual = residual[WINDOW:]

    placebo_seed = SEED + (12 if name == "FIN-0012" else 24)
    placeholders = placebo_net(
        dates, close, funding, start_idx, n_longs, n_shorts, gross_per_leg,
        lambda i, d: raw_weight_builder(close if name == "FIN-0012" else funding, dates, i),
        placebo_seed,
    )

    def info_ratio(a, b):
        x = [u - v for u, v in zip(a, b)]
        sd = statistics.stdev(x) if len(x) > 1 else 0.0
        return (statistics.mean(x) / sd) * math.sqrt(365.25) if sd else 0.0

    smg = strat["gross_metrics"]
    smn = strat["net_metrics"]
    bmn = btc["net_metrics"]
    emn = ew["net_metrics"]

    reconciliation = {
        "candidate": name,
        "accounting_identity": {
            "gross_final_equity": strat["gross_metrics"]["final_equity"],
            "net_final_equity": strat["net_metrics"]["final_equity"],
            "reported_cost_fraction": strat["costs"],
            "reported_funding_pnl": strat["funding_pnl"],
        },
    }

    if name == "FIN-0012":
        observed = {
            "cumulative_return": smn["cumulative_return"],
            "cagr": smn["cagr"],
            "sharpe": smn["sharpe"],
            "max_drawdown": smn["max_drawdown"],
            "turnover": strat["turnover"],
            "transaction_costs": strat["costs"],
            "funding_pnl": strat["funding_pnl"],
        }
        mismatches = {
            k: {"observed": observed[k], "accepted": ACCEPTED_FIN12[k]}
            for k in observed
            if abs(observed[k] - ACCEPTED_FIN12[k]) > 1e-9
        }
        reconciliation["authoritative_fin12_gate"] = {
            "all_within_1e-9": not mismatches,
            "values": observed,
            "accepted": ACCEPTED_FIN12,
            "mismatches": mismatches,
        }
        if mismatches:
            raise RuntimeError("FIN-0012 NET RECONCILIATION GATE FAILED: " + json.dumps(mismatches, sort_keys=True))

    result = {
        "candidate": name,
        "oos": {
            "start": strat["net_curve"] and dates[start_idx],
            "end": dates[-1],
            "observations": len(strat["net_curve"]),
            "full_history_start": dates[0],
            "full_history_end": dates[-1],
        },
        "gross_metrics": smg,
        "net_metrics": smn,
        "benchmarks_net": {"btc": bmn, "equal_weight": emn},
        "benchmark_relative_net": {
            "vs_btc_relative_wealth_return": (1 + smn["cumulative_return"]) / (1 + bmn["cumulative_return"]) - 1,
            "vs_equal_weight_relative_wealth_return": (1 + smn["cumulative_return"]) / (1 + emn["cumulative_return"]) - 1,
            "information_ratio_vs_btc": info_ratio(strat["net_returns"], btc["net_returns"]),
            "information_ratio_vs_equal_weight": info_ratio(strat["net_returns"], ew["net_returns"]),
        },
        "rolling_factor_metrics_net": v1.metrics(usable_residual) if usable_residual else {},
        "rolling_factor_warmup_observations": WINDOW,
        "rolling_beta_summary": {
            "btc_mean": statistics.mean(b[0] for b in betas[WINDOW:]),
            "btc_median": statistics.median(b[0] for b in betas[WINDOW:]),
            "equal_weight_mean": statistics.mean(b[1] for b in betas[WINDOW:]),
            "equal_weight_median": statistics.median(b[1] for b in betas[WINDOW:]),
            "r2_mean": statistics.mean(x for x in r2s[WINDOW:] if x is not None),
            "r2_median": statistics.median(x for x in r2s[WINDOW:] if x is not None),
        },
        "placebo_net": {
            "n": len(placeholders),
            "seed": placebo_seed,
            "actual_cumulative_percentile": percentile(smn["cumulative_return"], [x["cum"] for x in placeholders]),
            "actual_sharpe_percentile": percentile(smn["sharpe"], [x["sharpe"] for x in placeholders]),
            "actual_mdd_percentile": percentile(smn["max_drawdown"], [x["mdd"] for x in placeholders]),
            "cum_mean": statistics.mean(x["cum"] for x in placeholders),
            "cum_median": statistics.median(x["cum"] for x in placeholders),
            "cum_p05": sorted(x["cum"] for x in placeholders)[int(0.05 * len(placeholders))],
            "cum_p95": sorted(x["cum"] for x in placeholders)[int(0.95 * len(placeholders)) - 1],
        },
        "execution": {
            "turnover": strat["turnover"],
            "transaction_costs": strat["costs"],
            "funding_pnl": strat["funding_pnl"],
            "terminal_liquidation_cost": strat["liquidation_cost"],
        },
        "reconciliation": reconciliation,
        "integrity": {
            "holdout_access": False,
            "candidate_mutation": False,
            "parameter_search": False,
            "universe_search": False,
            "direction_search": False,
        },
    }
    return result, dates[start_idx:], strat, btc, ew, placeholders

def classification(r):
    r2 = r["rolling_beta_summary"]["r2_mean"]
    rs = r["rolling_factor_metrics_net"].get("sharpe", 0.0)
    if r2 >= 0.70:
        return "benchmark-dominant"
    if r2 >= 0.40:
        return "mixed exposure"
    return "relatively independent" if rs > 0 else "relatively independent / weak"

def main():
    OUT.mkdir(parents=True, exist_ok=True)

    monthly = {s: v1.load_monthly_close(s) for s in SYMBOLS}
    monthly_f = {s: v1.load_monthly_funding(s) for s in SYMBOLS}
    deep = {s: v1.load_deep_close(s) for s in SYMBOLS}
    deep_f = {s: v1.load_deep_funding(s) for s in SYMBOLS}

    r12, d12, s12, b12, e12, p12 = process("FIN-0012", monthly, monthly_f, v1.fin12_weights, 2, 2, 0.25)
    r24, d24, s24, b24, e24, p24 = process("FIN-0024", deep, deep_f, v1.fin24_weights, 3, 3, 1.0 / 6.0)

    manifest_files = []
    for root in [v1.MONTHLY_ROOT, v1.DEEP_ROOT]:
        for p in sorted(root.rglob("*")):
            if p.is_file():
                raw = p.read_bytes()
                manifest_files.append({"path": str(p), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    manifest = {
        "generated_from_github_sha": __import__("os").environ.get("GITHUB_SHA"),
        "files": manifest_files,
        "oos_start": OOS_START,
        "oos_end": END,
        "placebos": PLACEBOS,
        "seed_base": SEED,
        "holdout_access": False,
        "candidate_mutation": False,
        "parameter_search": False,
        "universe_search": False,
        "direction_search": False,
    }
    mb = json.dumps(manifest, sort_keys=True, indent=2).encode() + b"\n"
    (OUT / "input_manifest.json").write_bytes(mb)
    msha = hashlib.sha256(mb).hexdigest()

    payload = {
        "version": "2.0",
        "input_manifest_sha256": msha,
        "method": {
            "factor_window": WINDOW,
            "placebos": PLACEBOS,
            "seed_base": SEED,
            "diagnostic_only": True,
            "accounting_reconciled": True,
            "candidate_mutation": False,
            "holdout_access": False,
        },
        "candidates": {"FIN-0012": r12, "FIN-0024": r24},
    }
    raw = json.dumps(payload, sort_keys=True, indent=2).encode() + b"\n"
    (OUT / "alpha_autopsy.json").write_bytes(raw)

    report = [
        "# Harmony Alpha Autopsy v2",
        "",
        "Diagnostic-only. Gross and net accounting are reported separately; all attribution and placebo statistics use net returns.",
        "",
        "## FIN-0012",
        json.dumps(r12, indent=2, sort_keys=True),
        f"Descriptive classification: **{classification(r12)}**",
        "",
        "## FIN-0024",
        json.dumps(r24, indent=2, sort_keys=True),
        f"Descriptive classification: **{classification(r24)}**",
        "",
        "## Accounting reconciliation",
        "FIN-0012 must match the accepted durability net-equity metrics within 1e-9. A gross/net discrepancy is accounting, not alpha.",
    ]
    (OUT / "alpha_autopsy_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")

    print(json.dumps(payload, indent=2, sort_keys=True))

if __name__ == "__main__":
    main()
