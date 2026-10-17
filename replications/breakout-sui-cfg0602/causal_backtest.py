#!/usr/bin/env python3
"""Causal, independently accounted SUIUSDT cfg_0602 evaluation.

Uses only public, hash-verified candle data and the frozen source configuration.
No optimization, account access, or live trading.
"""
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from replicate import CAPITAL, COSTS, END, HOUR, START, iso, load, signals

HERE = Path(__file__).resolve().parent
INITIAL_CAPITAL = CAPITAL
EXIT_LOOKBACK = 48
YEAR_MS = 365.0 * 24 * HOUR


def utc_day(ms):
    return datetime.fromtimestamp(ms / 1000.0, timezone.utc).strftime("%Y-%m-%d")


def run_causal(candles, signal_indices, multiplier, start_ts=START, end_ts=END,
               exit_lookback=EXIT_LOOKBACK, initial_capital=INITIAL_CAPITAL):
    """Simulate close-confirmed signals, next-open fills and event-time equity.

    Candle timestamps denote candle opens. Open marks are recorded after pending
    orders execute; close marks are recorded at open timestamp + 1 hour - 1 ms.
    Thus a next-open fill is never valued against the previous candle's close.
    The final position is liquidated at the final close, with costs.
    """
    if not candles:
        raise ValueError("No candles supplied")
    stamps = [int(c["timestamp"]) for c in candles]
    opens = [float(c["open"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]

    indices = [i for i, ts in enumerate(stamps) if start_ts <= ts <= end_ts]
    if not indices:
        raise ValueError("No candles fall inside the frozen evaluation window")
    first_i, last_i = indices[0], indices[-1]
    if any(stamps[i] - stamps[i - 1] != HOUR for i in range(first_i + 1, last_i + 1)):
        raise ValueError("Gaps or duplicate timestamps in evaluation window")

    fee_rate = 0.001 * multiplier
    slip_rate = 0.0005 * multiplier
    cash = float(initial_capital)
    qty = 0.0
    active = False
    pending_exit = False
    pending_exit_signal_i = None
    pending_entry = False
    pending_entry_signal_i = None
    entry = None
    trades = []
    equity_values = []
    equity_times = []
    total_fees = 0.0
    total_slippage = 0.0
    total_turnover = 0.0
    exposure_bars = 0
    unfilled_terminal_signal = False

    def mark(value, time_ms):
        if not math.isfinite(value) or value < -1e-7:
            raise RuntimeError(f"Invalid equity value {value} at {time_ms}")
        equity_values.append(max(0.0, value))
        equity_times.append(time_ms)

    def enter(i, signal_i):
        nonlocal cash, qty, active, entry, total_fees, total_slippage, total_turnover
        raw = opens[i]
        eff = raw * (1.0 + slip_rate)
        if raw <= 0 or eff <= 0 or cash <= 0:
            raise RuntimeError("Non-positive entry price or available cash")
        cash_before = cash
        q = cash_before / (eff * (1.0 + fee_rate))
        fee = eff * q * fee_rate
        slip = (eff - raw) * q
        spend = eff * q + fee
        cash = cash_before - spend
        if abs(cash) <= max(1e-9, initial_capital * 1e-12):
            cash = 0.0
        if cash < -1e-7:
            raise RuntimeError(f"Entry costs overdraw cash: {cash}")
        qty = q
        active = True
        entry = {
            "signal_time": iso(stamps[signal_i] + HOUR - 1),
            "entry_time": iso(stamps[i]),
            "raw_entry_price": raw,
            "effective_entry_price": eff,
            "quantity": q,
            "entry_fee": fee,
            "entry_slippage": slip,
            "capital_at_entry": cash_before,
        }
        total_fees += fee
        total_slippage += slip
        total_turnover += raw * q

    def exit_position(raw, time_ms, signal_i, reason):
        nonlocal cash, qty, active, entry, total_fees, total_slippage, total_turnover
        if not active or entry is None or qty <= 0:
            raise RuntimeError("Exit requested without an open position")
        eff = raw * (1.0 - slip_rate)
        fee = eff * qty * fee_rate
        slip = (raw - eff) * qty
        proceeds = qty * eff - fee
        pnl = proceeds - entry["capital_at_entry"]
        trade = {
            **entry,
            "exit_signal_time": iso(stamps[signal_i] + HOUR - 1) if signal_i is not None else None,
            "exit_time": iso(time_ms),
            "raw_exit_price": raw,
            "effective_exit_price": eff,
            "exit_fee": fee,
            "exit_slippage": slip,
            "net_pnl": pnl,
            "return_on_capital": pnl / entry["capital_at_entry"] if entry["capital_at_entry"] else None,
            "exit_reason": reason,
        }
        trades.append(trade)
        cash = proceeds
        qty = 0.0
        active = False
        entry = None
        total_fees += fee
        total_slippage += slip
        total_turnover += raw * trade["quantity"]

    for i in range(first_i, last_i + 1):
        # Orders generated after the preceding candle close execute at this bar's open.
        if pending_exit:
            exit_position(opens[i], stamps[i], pending_exit_signal_i, "channel_48_next_open")
            pending_exit = False
            pending_exit_signal_i = None
        if pending_entry:
            if active:
                raise RuntimeError("Pending entry would overlap an open position")
            enter(i, pending_entry_signal_i)
            pending_entry = False
            pending_entry_signal_i = None

        # Actual open mark after execution/costs, followed by a close mark.
        mark(cash + (qty * opens[i] if active else 0.0), stamps[i])
        if active:
            exposure_bars += 1
        close_ts = stamps[i] + HOUR - 1
        mark(cash + (qty * closes[i] if active else 0.0), close_ts)

        has_exit = active and i >= exit_lookback and closes[i] < min(lows[i - exit_lookback:i])
        if has_exit and i < last_i:
            pending_exit = True
            pending_exit_signal_i = i
        elif has_exit and i == last_i:
            # No next open exists inside this window; boundary-close below handles liquidation.
            pass

        # Match the frozen source's state transition: a signal can be acted on
        # after an exit trigger, but not while an unexited position remains open.
        if i in signal_indices and i < last_i and (not active or has_exit):
            pending_entry = True
            pending_entry_signal_i = i
        if i == last_i and i in signal_indices and not active:
            unfilled_terminal_signal = True

    # Liquidate on the last candle close. Account for the cost in final equity.
    if active:
        last = last_i
        reason = "channel_48_window_boundary" if (
            last >= exit_lookback and closes[last] < min(lows[last - exit_lookback:last])
        ) else "end_of_window"
        exit_position(closes[last], stamps[last] + HOUR - 1, last if reason.startswith("channel_48") else None, reason)
        mark(cash, stamps[last] + HOUR - 1)

    if not equity_values:
        raise RuntimeError("Equity series is empty")
    if any(b + 1e-9 < 0 for b in equity_values):
        raise RuntimeError("Negative equity encountered")

    daily_equity = {}
    for ts, eq in zip(equity_times, equity_values):
        daily_equity[utc_day(ts)] = eq
    days = sorted(daily_equity)
    daily_returns = {}
    for previous, current in zip(days, days[1:]):
        prev_eq, current_eq = daily_equity[previous], daily_equity[current]
        if prev_eq > 0:
            daily_returns[current] = current_eq / prev_eq - 1.0
    dr = list(daily_returns.values())
    mean = sum(dr) / len(dr) if dr else 0.0
    sample_sd = math.sqrt(sum((x - mean) ** 2 for x in dr) / (len(dr) - 1)) if len(dr) > 1 else 0.0
    sharpe = mean / sample_sd * math.sqrt(365.0) if sample_sd > 0 else None

    peak = float(initial_capital)
    max_dd = 0.0
    for eq in equity_values:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)
    final_equity = equity_values[-1]
    net_return = final_equity / initial_capital - 1.0
    duration_years = (equity_times[-1] - equity_times[0]) / YEAR_MS
    annualized_return = (final_equity / initial_capital) ** (1.0 / duration_years) - 1.0 if duration_years > 0 and final_equity > 0 else None
    wins = sum(1 for t in trades if t["net_pnl"] > 0)
    losses = sum(-t["net_pnl"] for t in trades if t["net_pnl"] < 0)
    gains = sum(t["net_pnl"] for t in trades if t["net_pnl"] > 0)
    return {
        "equity_trace": [{"timestamp_ms": ts, "equity": eq} for ts, eq in zip(equity_times, equity_values)],
        "cost_multiplier": multiplier,
        "fee_bps_per_side": 10.0 * multiplier,
        "slippage_bps_per_side": 5.0 * multiplier,
        "initial_capital": initial_capital,
        "final_equity_after_liquidation": final_equity,
        "net_return": net_return,
        "annualized_return": annualized_return,
        "daily_sharpe": sharpe,
        "max_drawdown": max_dd,
        "closed_trades": len(trades),
        "win_rate": wins / len(trades) if trades else None,
        "profit_factor": gains / losses if losses > 0 else None,
        "exposure_fraction_of_bars": exposure_bars / len(indices),
        "exposure_bars": exposure_bars,
        "evaluated_bars": len(indices),
        "total_fees_quote": total_fees,
        "fee_drag_initial_capital": total_fees / initial_capital,
        "total_slippage_quote": total_slippage,
        "slippage_drag_initial_capital": total_slippage / initial_capital,
        "turnover_multiple_initial_capital": total_turnover / initial_capital,
        "first_evaluation_bar_open": iso(stamps[first_i]),
        "last_evaluation_bar_open": iso(stamps[last_i]),
        "equity_first_timestamp": iso(equity_times[0]),
        "equity_last_timestamp": iso(equity_times[-1]),
        "daily_equity": daily_equity,
        "daily_returns": daily_returns,
        "trades": trades,
        "unfilled_terminal_signal": unfilled_terminal_signal,
    }


def compact(run):
    return {k: v for k, v in run.items() if k not in ("daily_equity", "daily_returns", "trades", "equity_trace")}


def main():
    all_sui, data_meta = load("SUIUSDT")
    window = [c for c in all_sui if START <= c["timestamp"] <= END]
    if len(window) != 5256:
        raise RuntimeError(f"Expected 5256 evaluation bars, got {len(window)}")

    variants = {
        "fold_local_indicators": (window, signals(window), START, END),
        "pre_window_indicator_warmup": (all_sui, signals(all_sui), START, END),
    }
    output = {
        "schema_version": "expansion-causal-breakout-evaluation-v1",
        "experiment_id": "HARMONY-BREAKOUT-SUI-CFG0602-CAUSAL-001",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "status": "COMPLETED_CAUSAL_SIMULATION_WITH_HOLDOUT_CAVEAT",
        "configuration": {
            "asset": "SUIUSDT", "timeframe": "1h", "config_id": "cfg_0602",
            "content_hash": "f61acc43bbc4f28b", "entry_rule": "classic_high_breakout",
            "entry_lookback_bars": 120, "atr_period": 14, "atr_buffer_multiple": 0.0,
            "confirmation": "two_close", "trend_filter": "none", "exit": "channel_48",
            "fill": "next_bar_open", "start_timestamp_ms": START, "end_timestamp_ms": END,
            "evaluation_rows": len(window), "source_holdout_exposure": "EXPOSED_OR_UNKNOWN",
        },
        "data_provenance": data_meta,
        "methodology": {
            "equity_timestamp_convention": "Mark at each candle open after any pending order executes, then at that candle close; close timestamp is open + 1 hour - 1 ms so it falls in the candle's UTC date.",
            "causality": "Signals are evaluated after a candle close and can only fill at the next candle open.",
            "costs": "Buy slippage raises the entry price; sell slippage lowers proceeds; entry and exit fees are each deducted exactly once from portfolio cash.",
            "liquidation": "Any open position is liquidated at the last in-window close, including exit costs.",
            "gross_reference": "Multiplier 0 rerun with zero fees and slippage, same frozen signal/exit rules.",
            "indicator_warmup": "Both fold-local and full-history indicator variants are reported; the latter uses earlier candles for indicators but only trades inside the fixed evaluation window.",
            "not_claimed": "This historical replay does not prove the original holdout was untouched and does not constitute prospective validation.",
        },
        "results": {},
    }
    daily_columns = {"date": []}
    for variant_name, (candles, signal_set, start_ts, end_ts) in variants.items():
        gross = run_causal(candles, signal_set, 0.0, start_ts, end_ts)
        scenario_results = []
        for mult in COSTS:
            run = run_causal(candles, signal_set, mult, start_ts, end_ts)
            item = compact(run)
            item["zero_cost_gross_return"] = gross["net_return"]
            item["return_difference_net_minus_zero_cost"] = run["net_return"] - gross["net_return"]
            scenario_results.append(item)
            output["results"].setdefault(variant_name, []).append({
                "summary": item,
                "trades": run["trades"],
                "daily_equity": run["daily_equity"],
                "daily_returns": run["daily_returns"],
            })
            prefix = f"{variant_name}_cost_{int(mult)}x"
            daily_columns[prefix + "_equity"] = run["daily_equity"]
            daily_columns[prefix + "_return"] = run["daily_returns"]
        prefix = f"{variant_name}_gross"
        daily_columns[prefix + "_equity"] = gross["daily_equity"]
        daily_columns[prefix + "_return"] = gross["daily_returns"]
        output.setdefault("variant_summaries", {})[variant_name] = {
            "zero_cost_gross_return": gross["net_return"],
            "cost_scenarios": scenario_results,
        }

    (HERE / "causal_backtest.json").write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    all_days = sorted(set().union(*(set(v) for k, v in daily_columns.items() if k != "date")))
    with (HERE / "causal_daily_returns.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["date"] + [k for k in daily_columns if k != "date"])
        writer.writeheader()
        for day in all_days:
            row = {"date": day}
            for col, values in daily_columns.items():
                if col == "date":
                    continue
                row[col] = values.get(day, "")
            writer.writerow(row)

    print(json.dumps({
        "status": output["status"],
        "experiment_id": output["experiment_id"],
        "data": data_meta,
        "summaries": output["variant_summaries"],
        "artifacts": ["causal_backtest.json", "causal_daily_returns.csv"],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
