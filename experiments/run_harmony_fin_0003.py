#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, "experiments")
from run_harmony_infra_0007 import SYMBOLS, months, download_one

EXPERIMENT_ID = "HARMONY-FIN-0003"
STRATEGY_ID = "BTC-ETH-LTC-XRP-BNB-BCH-ADA-DOGE-CSMOM-TOP2-DAILY-V1"
LOOKBACK = 20
OOS_FRACTION = 0.30
FEE = 0.0006
SLIPPAGE = 0.0005
TRADE_COST = FEE + SLIPPAGE


def digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def build_panel():
    results = []
    for s in SYMBOLS:
        for y, m in months():
            results.append(download_one(s, y, m))
    maps = {s: {} for s in SYMBOLS}
    for r in results:
        maps[r["symbol"]].update(r["closes"])
    common = None
    for s in SYMBOLS:
        ds = set(maps[s])
        common = ds if common is None else common & ds
    dates = sorted(common)
    if len(dates) != 1765 or dates[0] != "2021-01-01" or dates[-1] != "2025-10-31":
        raise AssertionError("panel identity does not match preregistered trace")
    return dates, maps


def daily_target(dates, close_maps, i):
    if i < LOOKBACK + 1:
        return {s: 0.0 for s in SYMBOLS}
    anchor = dates[i - LOOKBACK - 1]
    current_prev = dates[i - 1]
    scores = []
    for s in SYMBOLS:
        r = close_maps[s][current_prev] / close_maps[s][anchor] - 1.0
        scores.append((r, s))
    scores.sort(key=lambda x: (-x[0], x[1]))
    chosen = [s for _, s in scores[:2]]
    w = {s: 0.0 for s in SYMBOLS}
    for s in chosen:
        w[s] = 0.5
    return w


def simulate(dates, close_maps, start_idx, mode):
    equity = 1.0
    prev_weights = {s: 0.0 for s in SYMBOLS}
    curve = []
    gross_returns = []
    turnover = []
    costs = []
    first = True

    for i in range(start_idx, len(dates)):
        d = dates[i]
        if first:
            period_return = 0.0
            first = False
        else:
            prev_d = dates[i - 1]
            period_return = sum(
                prev_weights[s] * (close_maps[s][d] / close_maps[s][prev_d] - 1.0)
                for s in SYMBOLS
            )
        equity_before = equity * (1.0 + period_return)

        if mode == "strategy":
            target = daily_target(dates, close_maps, i)
        elif mode == "ew_bh":
            target = {s: 1.0 / len(SYMBOLS) for s in SYMBOLS}
        elif mode == "btc_bh":
            target = {s: (1.0 if s == "BTCUSDT" else 0.0) for s in SYMBOLS}
        else:
            raise ValueError(mode)

        delta = sum(abs(target[s] - prev_weights[s]) for s in SYMBOLS)
        c = TRADE_COST * delta
        equity = equity_before * (1.0 - c)
        curve.append(equity)
        gross_returns.append(equity / (curve[-2] if len(curve) > 1 else 1.0) - 1.0)
        turnover.append(delta * 0.5)
        costs.append(c)
        prev_weights = target

    # Terminal liquidation at the final close, with the same one-way execution assumptions.
    liquidation_delta = sum(abs(prev_weights[s]) for s in SYMBOLS)
    liq_cost = TRADE_COST * liquidation_delta
    equity *= (1.0 - liq_cost)
    curve[-1] = equity
    costs[-1] += liq_cost

    peak = curve[0]
    mdd = 0.0
    for x in curve:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1.0)

    r = gross_returns[1:] if len(gross_returns) > 1 else []
    stdev = statistics.stdev(r) if len(r) >= 2 else 0.0
    sharpe = (statistics.mean(r) / stdev) * math.sqrt(365.0) if stdev else 0.0

    days = max(1, len(curve) - 1)
    cagr = curve[-1] ** (365.25 / days) - 1.0

    return {
        "final_equity": curve[-1],
        "cumulative_return": curve[-1] - 1.0,
        "cagr": cagr,
        "max_drawdown": mdd,
        "sharpe": sharpe,
        "daily_observations": len(curve),
        "total_one_way_turnover": sum(turnover),
        "mean_daily_one_way_turnover": statistics.mean(turnover),
        "total_execution_cost_fraction": sum(costs),
        "curve": curve,
    }


def main():
    dates, close_maps = build_panel()
    split = int(math.floor(len(dates) * (1.0 - OOS_FRACTION)))
    oos_dates = dates[split:]

    strategy = simulate(dates, close_maps, split, "strategy")
    ew_bh = simulate(dates, close_maps, split, "ew_bh")
    btc_bh = simulate(dates, close_maps, split, "btc_bh")

    out = Path("artifacts/HARMONY-FIN-0003")
    out.mkdir(parents=True, exist_ok=True)

    strategy_public = {k: v for k, v in strategy.items() if k != "curve"}
    ew_public = {k: v for k, v in ew_bh.items() if k != "curve"}
    btc_public = {k: v for k, v in btc_bh.items() if k != "curve"}

    result = {
        "experiment_id": EXPERIMENT_ID,
        "strategy_id": STRATEGY_ID,
        "data": {
            "symbol_count": len(SYMBOLS),
            "symbols": SYMBOLS,
            "panel_rows": len(dates),
            "panel_start": dates[0],
            "panel_end": dates[-1],
            "oos_rows": len(oos_dates),
            "oos_start": oos_dates[0],
            "oos_end": oos_dates[-1],
        },
        "configuration": {
            "lookback_days": LOOKBACK,
            "oos_fraction": OOS_FRACTION,
            "fee_rate": FEE,
            "slippage_rate": SLIPPAGE,
            "rebalance": "daily_at_close",
            "terminal_liquidation": True,
            "parameters_fit_on_oos": False,
            "signal_uses_current_close": False,
        },
        "oos_strategy": strategy_public,
        "oos_equal_weight_bh": ew_public,
        "oos_btc_bh": btc_public,
        "comparisons": {
            "strategy_minus_equal_weight_cumulative_return":
                strategy["cumulative_return"] - ew_bh["cumulative_return"],
            "strategy_minus_btc_cumulative_return":
                strategy["cumulative_return"] - btc_bh["cumulative_return"],
        },
        "integrity": {
            "fixed_universe": True,
            "no_current_market_universe_discovery": True,
            "no_forward_fill": True,
            "costs_explicit": True,
            "lookahead_control": True,
            "post_convergence_optimization": False,
        },
    }

    identity = {
        "experiment_id": EXPERIMENT_ID,
        "strategy_id": STRATEGY_ID,
        "strategy_digest": digest({
            "strategy_id": STRATEGY_ID,
            "universe": SYMBOLS,
            "lookback_days": LOOKBACK,
            "portfolio_rule": "long top 2 equal-weight; all other weights zero",
            "rebalance": "daily at close",
            "fee_rate": FEE,
            "slippage_rate": SLIPPAGE,
        }),
        "data_trace": {
            "panel_sha256": digest({
                "dates": dates,
                "symbols": SYMBOLS,
                "close_hashes": {
                    s: digest(close_maps[s]) for s in SYMBOLS
                },
            })
        },
        "validation": "chronological_70_30_oos_fixed_parameters",
    }

    (out / "identity.json").write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n")
    (out / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
