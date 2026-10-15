#!/usr/bin/env python3
"""One-shot, preregistered historical screen of extreme-mover relative spreads."""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

from experiments.pinned_okx_panel import (
    BAR_DELTA,
    BOOTSTRAPS,
    BOOTSTRAP_BLOCK_DAYS,
    DataGateBlocked,
    FEE_BPS,
    OUT,
    SLIPPAGE_BPS,
    SYMBOLS,
    finite,
    load_aligned_data,
    sha256_bytes,
    utc_now,
    write_json,
)

EXPERIMENT_ID = "HARMONY-EXTREME-RELATIVE-SPREAD-001"
MAX_HOLD_BARS = 96
ATR_MULTIPLIER = 3.0
MIN_OOS_TRADES = 50
BOOTSTRAP_REPS = 2000
BOOTSTRAP_BLOCK_DAYS = 5
BOOTSTRAP_SEED = 1401


def build_pairs(return24: np.ndarray, symbols: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """Select top and bottom 24-hour movers at each completed bar, with stable ties."""
    n, m = return24.shape
    top = np.full(n, -1, dtype=int)
    bottom = np.full(n, -1, dtype=int)
    for t in range(n):
        ranked = [j for j in range(m) if finite(return24[t, j])]
        ranked.sort(key=lambda j: (-float(return24[t, j]), symbols[j]))
        if len(ranked) >= 2:
            top[t], bottom[t] = ranked[0], ranked[-1]
    return top, bottom


def lagged_spread_returns(close: np.ndarray, top: np.ndarray, bottom: np.ndarray) -> np.ndarray:
    """Return interval t using only the pair known at t-1; never rank backward."""
    n = len(top)
    out = np.full(n, np.nan, dtype=float)
    for t in range(1, n):
        ti, bi = int(top[t - 1]), int(bottom[t - 1])
        if ti < 0 or bi < 0:
            continue
        out[t] = math.log(close[t, ti] / close[t - 1, ti]) - math.log(close[t, bi] / close[t - 1, bi])
    return out


def primary_turn_direction(four_hour_return: float, current_return: float, previous_return: float) -> int:
    """Return +1/-1 only for a local turn aligned with the 4-hour context."""
    if not all(finite(x) for x in (four_hour_return, current_return, previous_return)):
        return 0
    if four_hour_return > 0 and current_return > 0 and previous_return <= 0:
        return 1
    if four_hour_return < 0 and current_return < 0 and previous_return >= 0:
        return -1
    return 0


def build_signals(features: dict[str, pd.DataFrame], index: pd.DatetimeIndex) -> dict:
    close = np.column_stack([features[s]["close"].to_numpy(dtype=float) for s in SYMBOLS])
    ret24 = np.column_stack([features[s]["return_24h"].to_numpy(dtype=float) for s in SYMBOLS])
    top, bottom = build_pairs(ret24, SYMBOLS)
    spread_ret = lagged_spread_returns(close, top, bottom)
    n = len(index)
    spread_close = np.zeros(n, dtype=float)
    four_hour = np.full(n, np.nan, dtype=float)
    for t in range(n):
        if t > 0:
            spread_close[t] = spread_close[t - 1] + (float(spread_ret[t]) if finite(spread_ret[t]) else 0.0)
        if t >= 15 and np.isfinite(spread_ret[t - 15:t + 1]).all():
            four_hour[t] = float(np.sum(spread_ret[t - 15:t + 1]))

    primary, breakout = {}, {}
    atr_fraction = np.column_stack([
        (features[s]["atr"] / features[s]["close"]).to_numpy(dtype=float) for s in SYMBOLS
    ])
    for t in range(16, n - 1):
        ti, bi = int(top[t]), int(bottom[t])
        if ti < 0 or bi < 0 or ti == bi:
            continue
        direction = primary_turn_direction(
            float(four_hour[t]), float(spread_ret[t]), float(spread_ret[t - 1])
        )
        if direction:
            primary[t] = {
                "direction": direction, "top_i": ti, "bottom_i": bi,
                "signal_time_utc": index[t].isoformat(), "signal_index": t,
                "spread_atr": float(atr_fraction[t, ti] + atr_fraction[t, bi]),
                "four_hour_return": float(four_hour[t]), "spread_return_15m": float(spread_ret[t]),
            }

        # The candle opens at :45 and is completed at the hour boundary.
        if index[t].minute == 45 and t >= 16 and np.isfinite(spread_close[t - 16:t + 1]).all():
            past = spread_close[t - 16:t]
            bdir = 1 if spread_close[t] > float(np.max(past)) else (
                -1 if spread_close[t] < float(np.min(past)) else 0
            )
            if bdir:
                breakout[t] = {
                    "direction": bdir, "top_i": ti, "bottom_i": bi,
                    "signal_time_utc": index[t].isoformat(), "signal_index": t,
                    "spread_atr": float(atr_fraction[t, ti] + atr_fraction[t, bi]),
                    "four_hour_return": float(four_hour[t]) if finite(four_hour[t]) else None,
                    "spread_return_15m": float(spread_ret[t]) if finite(spread_ret[t]) else None,
                }
    return {
        "top_i": top, "bottom_i": bottom, "spread_return": spread_ret,
        "spread_close": spread_close, "four_hour_return": four_hour,
        "signals": {
            "primary_turn_4h_context_15m_change": primary,
            "baseline_hourly_4h_breakout": breakout,
            "baseline_flat_no_signal": {},
        },
    }


def bootstrap_mean_ci(values: np.ndarray, seed: int = BOOTSTRAP_SEED,
                      reps: int = BOOTSTRAP_REPS, block_days: int = BOOTSTRAP_BLOCK_DAYS) -> dict:
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < max(30, block_days * 4):
        return {"status": "inconclusive_insufficient_daily_observations", "observations": int(len(x))}
    rng = np.random.default_rng(seed)
    blocks = int(math.ceil(len(x) / block_days))
    max_start = len(x) - block_days + 1
    means = np.empty(reps, dtype=float)
    for b in range(reps):
        starts = rng.integers(0, max_start, size=blocks)
        ix = np.concatenate([np.arange(s, s + block_days) for s in starts])[:len(x)]
        means[b] = float(np.mean(x[ix]))
    return {
        "status": "computed", "observations": int(len(x)),
        "method": "moving_block_bootstrap_of_daily_returns",
        "block_days": block_days, "replications": reps, "seed": seed,
        "ci_95": [float(v) for v in np.quantile(means, [0.025, 0.975])],
    }


def paired_bootstrap(primary: np.ndarray, comparator: np.ndarray) -> dict:
    a, b = np.asarray(primary, dtype=float), np.asarray(comparator, dtype=float)
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    valid = np.isfinite(a) & np.isfinite(b)
    a, b = a[valid], b[valid]
    primary_ci = bootstrap_mean_ci(a)
    diff_ci = bootstrap_mean_ci(a - b, seed=BOOTSTRAP_SEED + 1)
    return {
        "observations": int(len(a)),
        "primary_mean_daily_return_ci_95": primary_ci.get("ci_95"),
        "primary_bootstrap_status": primary_ci.get("status"),
        "primary_minus_hourly_breakout_mean_daily_return_ci_95": diff_ci.get("ci_95"),
        "difference_bootstrap_status": diff_ci.get("status"),
        "primary_bootstrap_details": primary_ci,
        "difference_bootstrap_details": diff_ci,
    }


def event_study(features: dict[str, pd.DataFrame], index: pd.DatetimeIndex,
                top: np.ndarray, bottom: np.ndarray) -> dict:
    close = np.column_stack([features[s]["close"].to_numpy(dtype=float) for s in SYMBOLS])
    forward, dates, rows = [], [], []
    # Once per 96 bars; daily non-overlapping forward 24-hour horizon.
    for t in range(96, len(index) - 96, 96):
        ti, bi = int(top[t]), int(bottom[t])
        if ti < 0 or bi < 0 or ti == bi:
            continue
        value = math.log(close[t + 96, ti] / close[t, ti]) - math.log(close[t + 96, bi] / close[t, bi])
        forward.append(value)
        dates.append(index[t].isoformat())
        rows.append({
            "formation_time_utc": index[t].isoformat(),
            "top_gainer": SYMBOLS[ti], "bottom_loser": SYMBOLS[bi],
            "forward_24h_log_relative_return": value,
        })
    x = np.asarray(forward, dtype=float)
    ci = bootstrap_mean_ci(x, seed=BOOTSTRAP_SEED + 2, reps=BOOTSTRAP_REPS, block_days=BOOTSTRAP_BLOCK_DAYS)
    summary = {
        "definition": "fixed top/bottom pair at formation; forward 96-bar log relative return; non-overlapping daily formations",
        "sample_count": int(len(x)),
        "mean_forward_24h_log_relative_return": float(np.mean(x)) if len(x) else None,
        "median_forward_24h_log_relative_return": float(np.median(x)) if len(x) else None,
        "positive_fraction": float(np.mean(x > 0)) if len(x) else None,
        "bootstrap_mean_ci_95": ci.get("ci_95"),
        "bootstrap_details": ci,
        "formations": rows,
    }
    return summary


def simulate(features: dict[str, pd.DataFrame], index: pd.DatetimeIndex, signals: dict[int, dict],
             start_i: int, end_i: int, slippage_bps: float) -> dict:
    """Single-position synthetic spread simulator; 50/50 legs, 1x gross notional."""
    if end_i <= start_i:
        return {"equity": np.array([]), "trades": [], "margin_breach_count": 0}
    arrays = {
        s: {c: features[s][c].to_numpy(dtype=float) for c in ("open", "high", "low", "close", "atr")}
        for s in SYMBOLS
    }
    one_way_cost = (FEE_BPS + float(slippage_bps)) / 10000.0
    nav = 1.0
    position = None
    pending = None
    equity, trades = [], []
    margin_breach_count = 0
    ruined = False

    def close_position(pos: dict, i: int, spread_exit: float, reason: str,
                       long_exit_ref: float | None = None, short_exit_ref: float | None = None) -> None:
        nonlocal nav, margin_breach_count, ruined
        # Spread-level stop fills are expressed as synthetic relative log return.
        # The 50/50 log-spread PnL proxy is explicit; spot candles do not encode
        # actual perp contract sizing, funding, margin or liquidation mechanics.
        gross_proxy = 0.5 * float(spread_exit)
        net_proxy = gross_proxy - 2.0 * one_way_cost
        breach = net_proxy <= -1.0
        if breach:
            margin_breach_count += 1
            ruined = True
            nav = 0.0
        else:
            nav = max(0.0, float(pos["nav_before_entry"] * (1.0 + net_proxy)))
        trades.append({
            "signal_time_utc": pos["signal_time_utc"],
            "entry_time_utc": index[pos["entry_i"]].isoformat(),
            "exit_time_utc": (index[i] + BAR_DELTA).isoformat(),
            "top_gainer_at_signal": SYMBOLS[pos["top_i"]],
            "bottom_loser_at_signal": SYMBOLS[pos["bottom_i"]],
            "long_leg": SYMBOLS[pos["long_i"]], "short_leg": SYMBOLS[pos["short_i"]],
            "direction": int(pos["direction"]), "exit_reason": reason,
            "held_bars": int(i - pos["entry_i"] + 1), "holding_hours": float((i - pos["entry_i"] + 1) * 0.25),
            "spread_exit_log_return_proxy": float(spread_exit),
            "gross_trade_return_proxy": float(gross_proxy),
            "net_trade_return_on_entry_nav": float(max(-1.0, net_proxy)),
            "unbounded_proxy_return_before_ruin_floor": float(net_proxy),
            "margin_breach_or_ruin": bool(breach),
            "entry_nav": float(pos["nav_before_entry"]), "exit_nav": float(nav),
            "fee_bps_per_side_per_leg": FEE_BPS, "slippage_bps_per_side_per_leg": float(slippage_bps),
        })

    for i in range(start_i, end_i):
        if pending is not None and position is None and not ruined:
            sig = pending
            long_i = int(sig["top_i"] if sig["direction"] > 0 else sig["bottom_i"])
            short_i = int(sig["bottom_i"] if sig["direction"] > 0 else sig["top_i"])
            long_open = arrays[SYMBOLS[long_i]]["open"][i]
            short_open = arrays[SYMBOLS[short_i]]["open"][i]
            atr_spread = max(0.0, float(sig["spread_atr"]))
            if finite(long_open) and finite(short_open) and long_open > 0 and short_open > 0 and atr_spread > 0:
                position = {
                    **sig, "long_i": long_i, "short_i": short_i, "entry_i": i,
                    "entry_long": float(long_open), "entry_short": float(short_open),
                    "nav_before_entry": float(nav),
                    "stop": -ATR_MULTIPLIER * atr_spread, "high_water": 0.0,
                }
            pending = None

        if position is not None:
            long_a = arrays[SYMBOLS[position["long_i"]]]
            short_a = arrays[SYMBOLS[position["short_i"]]]
            op_l, op_s = long_a["open"][i], short_a["open"][i]
            hi_l, hi_s = long_a["high"][i], short_a["high"][i]
            lo_l, lo_s = long_a["low"][i], short_a["low"][i]
            cl_l, cl_s = long_a["close"][i], short_a["close"][i]
            el, es = position["entry_long"], position["entry_short"]
            open_spread = math.log(op_l / el) - math.log(op_s / es)
            # Conservative adverse bar combination: long leg low and short leg high.
            low_spread = math.log(lo_l / el) - math.log(hi_s / es)
            close_spread = math.log(cl_l / el) - math.log(cl_s / es)
            reason, exit_spread = None, None
            if open_spread <= position["stop"]:
                reason, exit_spread = "gap_through_stop_at_open", open_spread
            elif low_spread <= position["stop"]:
                reason, exit_spread = "intrabar_adverse_spread_stop", float(position["stop"])
            elif (i - position["entry_i"] + 1) >= MAX_HOLD_BARS:
                reason, exit_spread = "maximum_24h_holding_period", close_spread
            elif i == end_i - 1:
                reason, exit_spread = "segment_terminal_liquidation", close_spread

            if reason is not None:
                close_position(position, i, float(exit_spread), reason)
                position = None
                current_equity = nav
            else:
                # Ratchet only from a completed close; activate this stop next bar.
                position["high_water"] = max(float(position["high_water"]), close_spread)
                atr_l = long_a["atr"][i] / cl_l if cl_l > 0 else 0.0
                atr_s = short_a["atr"][i] / cl_s if cl_s > 0 else 0.0
                atr_now = max(0.0, float(atr_l + atr_s))
                if finite(atr_now):
                    position["stop"] = max(float(position["stop"]),
                                            float(position["high_water"] - ATR_MULTIPLIER * atr_now))
                # Entry cost is included from the start of the active position.
                mark_proxy = 0.5 * close_spread
                current_equity = max(0.0, position["nav_before_entry"] * (1.0 - one_way_cost + mark_proxy))
                if current_equity <= 0:
                    margin_breach_count += 1
                    ruined = True
                    current_equity = 0.0
        else:
            current_equity = nav

        equity.append(float(current_equity))
        if position is None and pending is None and not ruined and i in signals and i + 1 < end_i:
            pending = dict(signals[i])

    if position is not None:
        final_i = end_i - 1
        long_close = arrays[SYMBOLS[position["long_i"]]]["close"][final_i]
        short_close = arrays[SYMBOLS[position["short_i"]]]["close"][final_i]
        final_spread = math.log(long_close / position["entry_long"]) - math.log(short_close / position["entry_short"])
        close_position(position, final_i, final_spread, "segment_terminal_liquidation")
        equity[-1] = float(nav)

    return {
        "equity": np.asarray(equity, dtype=float), "trades": trades,
        "margin_breach_count": int(margin_breach_count), "ruined": bool(ruined),
    }


def daily_path(equity: np.ndarray, index: pd.DatetimeIndex, start_i: int) -> pd.DataFrame:
    if len(equity) == 0:
        return pd.DataFrame(columns=["date_utc", "equity", "daily_return"])
    timestamps = index[start_i:start_i + len(equity)] + BAR_DELTA
    series = pd.Series(equity, index=timestamps).resample("1D").last().dropna()
    returns = series.pct_change()
    if len(returns):
        returns.iloc[0] = float(series.iloc[0]) - 1.0
    returns = returns.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return pd.DataFrame({
        "date_utc": [x.date().isoformat() for x in series.index],
        "equity": series.to_numpy(dtype=float), "daily_return": returns.to_numpy(dtype=float),
    })


def performance_metrics(sim: dict, index: pd.DatetimeIndex, start_i: int, end_i: int) -> tuple[dict, pd.DataFrame]:
    equity = np.asarray(sim["equity"], dtype=float)
    daily = daily_path(equity, index, start_i)
    trades = sim["trades"]
    final_nav = float(equity[-1]) if len(equity) else 1.0
    daily_returns = daily["daily_return"].to_numpy(dtype=float) if not daily.empty else np.array([])
    sd = float(np.std(daily_returns, ddof=1)) if len(daily_returns) > 1 else 0.0
    sharpe = float(np.mean(daily_returns) / sd * math.sqrt(365.25)) if sd > 0 else 0.0
    path = np.r_[1.0, daily["equity"].to_numpy(dtype=float)] if not daily.empty else np.array([1.0])
    drawdown = path / np.maximum.accumulate(path) - 1.0
    trade_returns = np.asarray([t["net_trade_return_on_entry_nav"] for t in trades], dtype=float)
    wins, losses = trade_returns[trade_returns > 0], trade_returns[trade_returns < 0]
    span_days = max(1.0, (end_i - start_i) * 0.25 / 24.0)
    cagr = final_nav ** (365.25 / span_days) - 1.0 if final_nav > 0 else -1.0
    result = {
        "days": int(len(daily)), "calendar_span_days": float(span_days),
        "final_nav": final_nav, "net_total_return": final_nav - 1.0, "net_cagr": float(cagr),
        "annualized_sharpe": float(sharpe), "maximum_drawdown": float(np.min(drawdown)),
        "closed_trade_count": int(len(trade_returns)),
        "expectancy_per_trade": float(np.mean(trade_returns)) if len(trade_returns) else None,
        "median_trade_return": float(np.median(trade_returns)) if len(trade_returns) else None,
        "win_rate": float(np.mean(trade_returns > 0)) if len(trade_returns) else None,
        "profit_factor": float(wins.sum() / abs(losses.sum())) if len(losses) and abs(losses.sum()) else None,
        "mean_daily_return": float(np.mean(daily_returns)) if len(daily_returns) else None,
        "margin_breach_or_ruin_count": int(sim.get("margin_breach_count", 0)),
        "ruined": bool(sim.get("ruined", False)),
    }
    return result, daily


def evaluate_gates(primary_base: dict, primary_high: dict, comparator_base: dict,
                   first_half: dict, second_half: dict, bootstrap: dict,
                   evidence_valid: bool) -> dict:
    def pos(metric: dict, key: str) -> bool:
        v = metric.get(key)
        return v is not None and finite(v) and float(v) > 0.0

    trade_count = primary_base.get("closed_trade_count")
    ci = bootstrap.get("primary_mean_daily_return_ci_95")
    diff_ci = bootstrap.get("primary_minus_hourly_breakout_mean_daily_return_ci_95")
    gates = {
        "pinned_data_evidence_valid": bool(evidence_valid),
        "at_least_50_primary_final_oos_trades": (
            isinstance(trade_count, (int, float)) and not isinstance(trade_count, bool) and trade_count >= MIN_OOS_TRADES
        ),
        "positive_absolute_primary_expectancy_at_5bps": pos(primary_base, "expectancy_per_trade"),
        "positive_primary_final_oos_net_return_at_5bps": pos(primary_base, "net_total_return"),
        "primary_daily_return_bootstrap_ci_lower_above_zero": (
            isinstance(ci, list) and len(ci) == 2 and finite(ci[0]) and float(ci[0]) > 0.0
        ),
        "primary_beats_hourly_breakout_bootstrap_ci_lower_above_zero": (
            isinstance(diff_ci, list) and len(diff_ci) == 2 and finite(diff_ci[0]) and float(diff_ci[0]) > 0.0
        ),
        "positive_primary_net_return_at_20bps": pos(primary_high, "net_total_return"),
        "positive_primary_annualized_sharpe_at_20bps": pos(primary_high, "annualized_sharpe"),
        "positive_primary_net_return_in_both_final_oos_halves_at_5bps": (
            pos(first_half, "net_total_return") and pos(second_half, "net_total_return")
        ),
        "no_primary_margin_breach_or_account_ruin": (
            int(primary_base.get("margin_breach_or_ruin_count", 0)) == 0
            and int(primary_high.get("margin_breach_or_ruin_count", 0)) == 0
        ),
        "primary_base_expectancy_is_not_null": primary_base.get("expectancy_per_trade") is not None,
        "hourly_breakout_baseline_metrics_present": comparator_base.get("net_total_return") is not None,
    }
    gates["all_predefined_gates_pass"] = all(gates.values())
    return gates


def run_research() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    started = utc_now()
    runner_hash = sha256_bytes(Path(__file__).read_bytes())
    spec_path = Path(__file__).resolve().parent / "HARMONY-EXTREME-RELATIVE-SPREAD-001.yaml"
    spec_hash = sha256_bytes(spec_path.read_bytes()) if spec_path.is_file() else None
    manifest = {
        "experiment_id": EXPERIMENT_ID, "started_at_utc": started,
        "github_sha": os.environ.get("GITHUB_SHA"), "github_ref": os.environ.get("GITHUB_REF"),
        "github_run_id": os.environ.get("GITHUB_RUN_ID"), "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "python_version": sys.version, "numpy_version": np.__version__, "pandas_version": pd.__version__,
        "runner_sha256": runner_hash, "spec_sha256": spec_hash,
        "symbols": SYMBOLS, "strategy_parameters": {
            "max_hold_bars": MAX_HOLD_BARS, "atr_multiplier": ATR_MULTIPLIER,
            "fee_bps_per_side_per_leg": FEE_BPS, "slippage_bps_per_side_per_leg": SLIPPAGE_BPS,
            "gross_notional_to_nav": 1.0, "leg_notional_to_nav": 0.5,
            "bootstrap_reps": BOOTSTRAP_REPS, "bootstrap_block_days": BOOTSTRAP_BLOCK_DAYS,
            "bootstrap_seed": BOOTSTRAP_SEED,
        },
    }
    write_json(OUT / "run_manifest.json", manifest)
    try:
        features, index, data_summary = load_aligned_data()
        write_json(OUT / "data_manifest.json", data_summary)
        design = build_signals(features, index)
        event = event_study(features, index, design["top_i"], design["bottom_i"])
        write_json(OUT / "event_study.json", {k: v for k, v in event.items() if k != "formations"})
        pd.DataFrame(event.get("formations", [])).to_csv(OUT / "event_study_formations.csv", index=False, lineterminator="\n")

        n = len(index)
        d_end, v_end = int(math.floor(n * 0.60)), int(math.floor(n * 0.80))
        split_mid = v_end + (n - v_end) // 2
        segments = {
            "discovery": (0, d_end), "validation": (d_end, v_end),
            "final_oos": (v_end, n), "final_oos_first_half": (v_end, split_mid),
            "final_oos_second_half": (split_mid, n),
        }
        all_metrics, trade_rows, daily_rows = {}, [], []
        daily_oos_base = {}
        for variant, signals in design["signals"].items():
            all_metrics[variant] = {}
            for segment, (si, ei) in segments.items():
                all_metrics[variant][segment] = {}
                for slip in SLIPPAGE_BPS:
                    if variant == "baseline_flat_no_signal":
                        sim = {"equity": np.ones(ei - si), "trades": [], "margin_breach_count": 0, "ruined": False}
                    else:
                        sim = simulate(features, index, signals, si, ei, float(slip))
                    metric, daily = performance_metrics(sim, index, si, ei)
                    all_metrics[variant][segment][str(int(slip))] = metric
                    for number, trade in enumerate(sim["trades"], 1):
                        trade_rows.append({
                            "variant": variant, "segment": segment, "slippage_bps_per_side_per_leg": int(slip),
                            "trade_number": number, **trade,
                        })
                    if not daily.empty:
                        daily.insert(0, "variant", variant)
                        daily.insert(1, "segment", segment)
                        daily.insert(2, "slippage_bps_per_side_per_leg", int(slip))
                        daily_rows.extend(daily.to_dict(orient="records"))
                    if segment == "final_oos" and int(slip) == 5:
                        daily_oos_base[variant] = daily["daily_return"].to_numpy(dtype=float) if not daily.empty else np.array([])

        primary_name = "primary_turn_4h_context_15m_change"
        baseline_name = "baseline_hourly_4h_breakout"
        primary = all_metrics[primary_name]
        baseline = all_metrics[baseline_name]
        bootstrap = paired_bootstrap(daily_oos_base.get(primary_name, np.array([])),
                                     daily_oos_base.get(baseline_name, np.array([])))
        evidence_valid = (
            data_summary.get("source_run_id") == 38643611024
            and data_summary.get("source_artifact_sha256") == "9e20b58052446be9574ac77f4d9d3fef5f1c22e5c7f1dfb5ec94a309e183edfd"
            and data_summary.get("source_summary_sha256") == "0074d5f245fa4323d7370491d320a756c50bcbb6233b3447a2ef62f2cae6c7fc"
            and data_summary.get("joint_missing_fraction_verified") == 0.0
        )
        gates = evaluate_gates(
            primary_base=primary["final_oos"]["5"], primary_high=primary["final_oos"]["20"],
            comparator_base=baseline["final_oos"]["5"],
            first_half=primary["final_oos_first_half"]["5"],
            second_half=primary["final_oos_second_half"]["5"],
            bootstrap=bootstrap, evidence_valid=evidence_valid,
        )
        trade_count = primary["final_oos"]["5"]["closed_trade_count"]
        if gates["all_predefined_gates_pass"]:
            decision = "eligible_for_future_only_confirmation"
        elif trade_count < MIN_OOS_TRADES:
            decision = "inconclusive_insufficient_trades"
        else:
            decision = "rejected_by_preregistered_historical_gates"

        summary = {
            "experiment_id": EXPERIMENT_ID, "status": "executed_exploratory_backtest",
            "decision": decision, "disposition": decision,
            "historical_result_is_independent_confirmation": False,
            "started_at_utc": started, "finished_at_utc": utc_now(), "run_manifest": manifest,
            "data_summary": data_summary,
            "chronology": {
                "discovery": {"start_index": 0, "end_index_exclusive": d_end},
                "validation": {"start_index": d_end, "end_index_exclusive": v_end},
                "final_oos": {"start_index": v_end, "end_index_exclusive": n},
                "final_oos_first_half": {"start_index": v_end, "end_index_exclusive": split_mid},
                "final_oos_second_half": {"start_index": split_mid, "end_index_exclusive": n},
            },
            "metrics": all_metrics, "bootstrap_final_oos_5bps": bootstrap,
            "event_study": {k: v for k, v in event.items() if k != "formations"},
            "promotion_gates": gates,
            "interpretation": {
                "return_model": "50% of fixed-pair relative log return is used as a synthetic long-short PnL proxy; this is not exact perp account equity",
                "costs": "fees and slippage are charged to both synthetic legs on entry and exit using the 1.0 gross-notional approximation",
                "not_modeled": ["perpetual funding", "spot-perpetual basis", "liquidation/margin mechanics", "actual venue fills", "borrow availability"],
                "limitations": [
                    "Source candles were previously examined in an unrelated strategy experiment; this run is exploratory, not independent confirmation.",
                    "Present-day 24-symbol universe creates listing/survivorship bias and cannot support market-wide claims.",
                    "Synthetic relative-spread PnL is a screening proxy; positive historical metrics cannot establish tradeability.",
                    "Any historical pass permits only a separately preregistered forward-only confirmation study; live orders are prohibited.",
                ],
            },
        }
        write_json(OUT / "summary.json", summary)
        pd.DataFrame(trade_rows).to_csv(OUT / "trades.csv", index=False, lineterminator="\n")
        pd.DataFrame(daily_rows).to_csv(OUT / "daily_equity.csv", index=False, lineterminator="\n")
        lines = [
            "# " + EXPERIMENT_ID, "", "## Decision: " + decision, "",
            "- Status: executed exploratory historical screen; not independent confirmation.",
            "- Common bars: " + str(data_summary["common_bar_count"]),
            "- History: " + str(data_summary["actual_common_start_utc"]) + " to " + str(data_summary["actual_common_end_candle_open_utc"]),
            "- Universe size: " + str(len(SYMBOLS)),
            "- Spread construction: interval t uses the pair selected at t-1; entry is next-bar open.",
            "- Costs: fee 5 bps per side per leg; slippage 5/10/20 bps per side per leg.",
            "- Model caveat: synthetic relative-log PnL proxy; perpetual funding and actual liquidation/fills are not modeled.",
            "",
            "| Strategy | Segment | Slippage bps/side/leg | Net return | CAGR | Sharpe | Max drawdown | Trades | Expectancy/trade | Margin breach/ruin |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for variant in design["signals"]:
            for segment in segments:
                for slip in SLIPPAGE_BPS:
                    m = all_metrics[variant][segment][str(int(slip))]
                    er = m["expectancy_per_trade"]
                    lines.append(
                        "| " + variant + " | " + segment + " | " + str(int(slip)) + " | "
                        + f"{m['net_total_return']:.3%}" + " | " + f"{m['net_cagr']:.3%}" + " | "
                        + f"{m['annualized_sharpe']:.3f}" + " | " + f"{m['maximum_drawdown']:.3%}" + " | "
                        + str(m["closed_trade_count"]) + " | " + (f"{er:.3%}" if er is not None else "NA")
                        + " | " + str(m["margin_breach_or_ruin_count"]) + " |"
                    )
        lines += ["", "## Fixed gate results", ""]
        lines += ["- " + key + ": " + str(value) for key, value in gates.items()]
        lines += [
            "", "## Forward 24-hour formation event study", "",
            "- Events: " + str(event["sample_count"]),
            "- Mean forward log relative return: " + (f"{event['mean_forward_24h_log_relative_return']:.6f}" if event["mean_forward_24h_log_relative_return"] is not None else "NA"),
            "- Median: " + (f"{event['median_forward_24h_log_relative_return']:.6f}" if event["median_forward_24h_log_relative_return"] is not None else "NA"),
            "- Positive fraction: " + (f"{event['positive_fraction']:.3%}" if event["positive_fraction"] is not None else "NA"),
            "- 95% block-bootstrap interval: " + str(event["bootstrap_mean_ci_95"]),
            "", "## Limitations", "",
            "- No parameter search; final OOS did not influence the fixed rules.",
            "- Earlier experiments used the same historical source candles; this does not qualify as blind confirmatory evidence.",
            "- The market panel has survivorship/listing bias.",
            "- Synthetic relative-log PnL is not exact perpetual-account equity; no funding, basis, borrow, liquidation or actual fill model.",
            "- No live orders are permitted. A historical pass would only qualify for a separately preregistered forward-only test.",
            "", "## Provenance", "",
            "- Runner SHA-256: " + runner_hash,
            "- Specification SHA-256: " + str(spec_hash),
            "- Source run/artifact: 38643611024 / 11934457001",
            "- Full results in summary.json; trade ledger, daily equity, event-study formations and data hashes are preserved as artifacts.",
        ]
        (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {"status": summary["status"], "decision": decision, "primary_final_oos_trades": trade_count,
                "all_gates_pass": gates["all_predefined_gates_pass"], "output": str(OUT)}
    except DataGateBlocked as exc:
        result = {
            "experiment_id": EXPERIMENT_ID, "status": "blocked_data_gate",
            "decision": "blocked_data_gate", "timestamp_utc": utc_now(),
            "reason": str(exc), "no_strategy_metrics_emitted": True, "run_manifest": manifest,
        }
        write_json(OUT / "data_gate.json", result)
        write_json(OUT / "summary.json", result)
        (OUT / "report.md").write_text("# " + EXPERIMENT_ID + "\n\n## BLOCKED — DATA GATE\n\n"
                                       + str(exc) + "\n\nNo strategy metrics were emitted.\n", encoding="utf-8")
        return result
    except Exception as exc:
        result = {
            "experiment_id": EXPERIMENT_ID, "status": "execution_error",
            "decision": "execution_error", "timestamp_utc": utc_now(),
            "error": str(exc), "traceback": traceback.format_exc(),
            "no_valid_strategy_conclusion": True, "run_manifest": manifest,
        }
        write_json(OUT / "execution_error.json", result)
        (OUT / "report.md").write_text("# " + EXPERIMENT_ID + "\n\n## EXECUTION ERROR\n\n"
                                       + str(exc) + "\n\nNo valid conclusion may be drawn.\n", encoding="utf-8")
        raise


if __name__ == "__main__":
    print(json.dumps(run_research(), indent=2, sort_keys=True))
