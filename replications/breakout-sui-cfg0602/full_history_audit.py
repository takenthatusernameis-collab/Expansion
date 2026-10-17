#!/usr/bin/env python3
"""Full-history, causal SUIUSDT cfg_0602 audit using public Binance market data.

No credentials, account access, live orders, parameter search, or optimization.
The official API listing boundary and every downloaded candle are validated.
"""
import csv
import hashlib
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from causal_backtest import run_causal
from replicate import CAPITAL, HOUR, canonical, iso, load, signals

HERE = Path(__file__).resolve().parent
LISTING_URL = "https://www.binance.com/en/support/announcement/detail/926445f1d3ef4fdbb2c505dc1fe4e741"
LISTING_START_MS = int(datetime(2023, 5, 3, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
API_BASES = (
    "https://data-api.binance.vision/api/v3/klines",
    "https://api.binance.com/api/v3/klines",
    "https://api-gcp.binance.com/api/v3/klines",
)
PAGE_LIMIT = 1000
COST_MULTIPLIERS = (0.0, 1.0, 2.0, 3.0)
SCENARIO_COSTS = {
    0.0: {"fee_bps_per_side": 0.0, "slippage_bps_per_side": 0.0},
    1.0: {"fee_bps_per_side": 10.0, "slippage_bps_per_side": 5.0},
    2.0: {"fee_bps_per_side": 20.0, "slippage_bps_per_side": 10.0},
    3.0: {"fee_bps_per_side": 30.0, "slippage_bps_per_side": 15.0},
}
YEAR_MS = 365.0 * 24 * HOUR


def normalize_timestamp_ms(value):
    """Normalize REST responses to ms, tolerating explicit microsecond timestamps."""
    result = int(value)
    return result // 1000 if result > 100_000_000_000_000 else result


def latest_closed_open_ms(now_ms=None):
    """Return the open time of the most recent fully closed 1-hour candle."""
    if now_ms is None:
        now_ms = time.time_ns() // 1_000_000
    return (int(now_ms) // HOUR) * HOUR - HOUR


def parse_api_kline(row):
    if not isinstance(row, list) or len(row) < 7:
        raise ValueError("Kline API row must contain at least seven fields")
    timestamp = normalize_timestamp_ms(row[0])
    close_timestamp = normalize_timestamp_ms(row[6])
    if timestamp % HOUR != 0:
        raise ValueError(f"Non-hour-aligned kline open time: {timestamp}")
    if close_timestamp != timestamp + HOUR - 1:
        raise ValueError(f"Unexpected close timestamp for candle {timestamp}: {close_timestamp}")
    candle = {"timestamp": timestamp}
    for key, idx in (("open", 1), ("high", 2), ("low", 3), ("close", 4), ("volume", 5)):
        candle[key] = float(row[idx])
    return candle


def validate_series(candles, expected_start=None, expected_end=None):
    if not candles:
        raise ValueError("No hourly candles were retrieved")
    timestamps = [int(c["timestamp"]) for c in candles]
    if any(b <= a for a, b in zip(timestamps, timestamps[1:])):
        raise ValueError("Duplicate or out-of-order candle timestamps")
    if any(b - a != HOUR for a, b in zip(timestamps, timestamps[1:])):
        raise ValueError("Missing hourly candles or non-hourly timestamp gap")
    for idx, candle in enumerate(candles):
        if timestamps[idx] % HOUR != 0:
            raise ValueError(f"Non-hour-aligned timestamp at row {idx}")
        values = [float(candle[k]) for k in ("open", "high", "low", "close", "volume")]
        if not all(math.isfinite(v) for v in values):
            raise ValueError(f"Non-finite OHLCV value at row {idx}")
        op, hi, lo, cl, volume = values
        if min(op, hi, lo, cl) <= 0 or volume < 0:
            raise ValueError(f"Non-positive price or negative volume at row {idx}")
        if hi < max(op, cl, lo) or lo > min(op, cl, hi):
            raise ValueError(f"Invalid OHLC bounds at row {idx}")
    if expected_start is not None and timestamps[0] != expected_start:
        raise ValueError(
            f"History starts at {iso(timestamps[0])}, expected listing-hour candle {iso(expected_start)}"
        )
    if expected_end is not None and timestamps[-1] != expected_end:
        raise ValueError(
            f"History ends at {iso(timestamps[-1])}, expected latest fully closed candle {iso(expected_end)}"
        )
    if expected_start is not None and expected_end is not None:
        expected_count = (expected_end - expected_start) // HOUR + 1
        if len(candles) != expected_count:
            raise ValueError(f"Expected {expected_count} candles for complete coverage, got {len(candles)}")
    return {
        "rows": len(candles),
        "first_timestamp": iso(timestamps[0]),
        "last_timestamp": iso(timestamps[-1]),
        "gap_count": 0,
        "duplicate_count": 0,
    }


def fetch_klines_from_base(base_url, symbol, start_ms, end_ms, page_limit=PAGE_LIMIT):
    """Fetch a continuous, bounded history. Returns candles and request count."""
    if start_ms % HOUR or end_ms % HOUR or end_ms < start_ms:
        raise ValueError("Start/end must be ordered, hour-aligned open timestamps")
    candles = []
    cursor = start_ms
    requests_made = 0
    while cursor <= end_ms:
        query = urllib.parse.urlencode({
            "symbol": symbol,
            "interval": "1h",
            "startTime": cursor,
            "endTime": end_ms + HOUR - 1,
            "limit": page_limit,
        })
        request = urllib.request.Request(
            base_url + "?" + query,
            headers={"User-Agent": "Expansion-Full-History-Audit/1.0", "Accept": "application/json"},
        )
        payload = None
        last_error = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    payload = response.read()
                break
            except urllib.error.HTTPError as exc:
                last_error = f"HTTP {exc.code}: {exc.reason}"
                if exc.code not in (408, 425, 429, 500, 502, 503, 504) or attempt == 2:
                    break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = repr(exc)
                if attempt == 2:
                    break
            time.sleep(0.5 * (attempt + 1))
        if payload is None:
            raise RuntimeError(f"Request failed at cursor {iso(cursor)} from {base_url}: {last_error}")
        try:
            rows = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid JSON response from {base_url}: {exc}") from exc
        requests_made += 1
        if not isinstance(rows, list) or not rows:
            raise RuntimeError(f"Empty or unexpected klines response at {iso(cursor)}: {str(rows)[:300]}")
        page = []
        for raw in rows:
            timestamp = normalize_timestamp_ms(raw[0]) if isinstance(raw, list) and raw else None
            if timestamp is None:
                raise RuntimeError("Malformed kline row without an open timestamp")
            if timestamp < cursor:
                raise RuntimeError(f"API returned a candle before requested cursor: {iso(timestamp)} < {iso(cursor)}")
            if timestamp > end_ms:
                continue
            page.append(parse_api_kline(raw))
        if not page:
            raise RuntimeError(f"No usable candles returned at requested cursor {iso(cursor)}")
        for candle in page:
            if candles and candle["timestamp"] != candles[-1]["timestamp"] + HOUR:
                raise RuntimeError(
                    f"Pagination overlap or gap: previous={iso(candles[-1]['timestamp'])}, "
                    f"next={iso(candle['timestamp'])}"
                )
            candles.append(candle)
        last_timestamp = candles[-1]["timestamp"]
        if last_timestamp == end_ms:
            break
        if last_timestamp > end_ms:
            raise RuntimeError("API returned data past the frozen end boundary")
        cursor = last_timestamp + HOUR
    validate_series(candles, start_ms, end_ms)
    return candles, requests_made


def fetch_complete_series(symbol, start_ms, end_ms, snapshot_ms):
    failures = []
    for base_url in API_BASES:
        try:
            candles, calls = fetch_klines_from_base(base_url, symbol, start_ms, end_ms)
            coverage = validate_series(candles, start_ms, end_ms)
            return candles, {
                "symbol": symbol,
                "provider": "Binance Spot public klines REST API",
                "endpoint": base_url,
                "requests": calls,
                "request_interval": "1h",
                "requested_start": iso(start_ms),
                "requested_end": iso(end_ms),
                "snapshot_utc": iso(snapshot_ms),
                "coverage": coverage,
                "canonical_sha256": hashlib.sha256(canonical(candles).encode("utf-8")).hexdigest(),
            }
        except Exception as exc:
            failures.append({"endpoint": base_url, "error": str(exc)[:1000]})
    raise RuntimeError("Every approved Binance public market-data endpoint failed: " + json.dumps(failures))


def compare_overlap(full_series, pinned_series):
    by_timestamp = {int(c["timestamp"]): c for c in full_series}
    samples = []
    missing = []
    compared = 0
    mismatch_count = 0
    for old in pinned_series:
        timestamp = int(old["timestamp"])
        current = by_timestamp.get(timestamp)
        if current is None:
            if len(missing) < 10:
                missing.append(iso(timestamp))
            continue
        compared += 1
        fields = {}
        for key in ("open", "high", "low", "close", "volume"):
            if not math.isclose(float(old[key]), float(current[key]), rel_tol=1e-12, abs_tol=1e-12):
                fields[key] = {"pinned": float(old[key]), "api": float(current[key])}
        if fields:
            mismatch_count += 1
            if len(samples) < 10:
                samples.append({"timestamp": iso(timestamp), "fields": fields})
    return {
        "pinned_rows": len(pinned_series),
        "overlap_rows_compared": compared,
        "missing_pinned_timestamps": sum(1 for old in pinned_series if int(old["timestamp"]) not in by_timestamp),
        "mismatch_rows": mismatch_count,
        "mismatch_samples": samples,
        "missing_timestamp_samples": missing,
        "absolute_tolerance": 1e-12,
        "relative_tolerance": 1e-12,
    }


def benchmark_metrics(candles, multiplier):
    first = candles[0]
    last = candles[-1]
    fee_rate = 0.001 * multiplier
    slip_rate = 0.0005 * multiplier
    start_price = float(first["open"])
    end_price = float(last["close"])
    if start_price <= 0 or end_price <= 0:
        raise ValueError("Benchmark boundary price is non-positive")
    gross_return = end_price / start_price - 1.0
    final_value = end_price * (1.0 - slip_rate) * (1.0 - fee_rate) / (
        start_price * (1.0 + slip_rate) * (1.0 + fee_rate)
    )
    net_return = final_value - 1.0
    duration_years = ((int(last["timestamp"]) + HOUR - 1) - int(first["timestamp"])) / YEAR_MS
    annualized = (1.0 + net_return) ** (1.0 / duration_years) - 1.0 if duration_years > 0 else None
    return {
        "cost_multiplier": multiplier,
        "fee_bps_per_side": SCENARIO_COSTS[multiplier]["fee_bps_per_side"],
        "slippage_bps_per_side": SCENARIO_COSTS[multiplier]["slippage_bps_per_side"],
        "first_timestamp": iso(int(first["timestamp"])),
        "last_timestamp": iso(int(last["timestamp"])),
        "buy_price_open": start_price,
        "sell_price_close": end_price,
        "gross_return": gross_return,
        "net_return": net_return,
        "annualized_net_return": annualized,
        "common_hourly_rows": len(candles),
    }


def summarize_strategy(result, multiplier):
    full_pnl = sum(float(t["net_pnl"]) for t in result["trades"])
    equity_pnl = float(result["final_equity_after_liquidation"]) - float(result["initial_capital"])
    if not math.isclose(full_pnl, equity_pnl, rel_tol=1e-8, abs_tol=1e-6):
        raise RuntimeError(
            f"Trade-level P&L does not reconcile to final equity at {multiplier}x: "
            f"trades={full_pnl}, equity={equity_pnl}"
        )
    winners = sorted((t for t in result["trades"] if t["net_pnl"] > 0), key=lambda t: t["net_pnl"], reverse=True)
    winning_profit = sum(float(t["net_pnl"]) for t in winners)
    top_two_profit = sum(float(t["net_pnl"]) for t in winners[:2])
    summary = {
        key: value for key, value in result.items()
        if key not in ("equity_trace", "daily_equity", "daily_returns", "trades")
    }
    summary.update({
        "total_net_trade_pnl": full_pnl,
        "winning_trade_profit_sum": winning_profit,
        "top_two_winners_net_pnl": top_two_profit,
        "top_two_winners_fraction_of_all_winning_profit": top_two_profit / winning_profit if winning_profit > 0 else None,
        "top_two_winners_fraction_of_total_net_pnl": top_two_profit / full_pnl if full_pnl != 0 else None,
        "top_winning_trades": winners[:2],
        "cost_multiplier": multiplier,
        "scenario_fee_bps_per_side": SCENARIO_COSTS[multiplier]["fee_bps_per_side"],
        "scenario_slippage_bps_per_side": SCENARIO_COSTS[multiplier]["slippage_bps_per_side"],
    })
    return summary


def write_market_csv(path, series_by_symbol):
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "symbol", "timestamp_ms", "timestamp_utc", "open", "high", "low", "close", "volume",
        ])
        writer.writeheader()
        for symbol in sorted(series_by_symbol):
            for candle in series_by_symbol[symbol]:
                writer.writerow({
                    "symbol": symbol,
                    "timestamp_ms": int(candle["timestamp"]),
                    "timestamp_utc": iso(int(candle["timestamp"])),
                    **{key: candle[key] for key in ("open", "high", "low", "close", "volume")},
                })


def write_trade_csv(path, results_by_multiplier):
    fields = [
        "cost_multiplier", "signal_time", "entry_time", "raw_entry_price", "effective_entry_price",
        "quantity", "entry_fee", "entry_slippage", "capital_at_entry", "exit_signal_time",
        "exit_time", "raw_exit_price", "effective_exit_price", "exit_fee", "exit_slippage",
        "net_pnl", "return_on_capital", "exit_reason",
    ]
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for multiplier, result in results_by_multiplier.items():
            for trade in result["trades"]:
                writer.writerow({"cost_multiplier": multiplier, **trade})


def write_daily_csv(path, results_by_multiplier):
    fields = ["utc_date"]
    for multiplier in results_by_multiplier:
        fields.extend([f"equity_{multiplier:g}x", f"daily_return_{multiplier:g}x"])
    days = sorted(set().union(*(set(result["daily_equity"]) for result in results_by_multiplier.values())))
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for day in days:
            row = {"utc_date": day}
            for multiplier, result in results_by_multiplier.items():
                row[f"equity_{multiplier:g}x"] = result["daily_equity"].get(day, "")
                row[f"daily_return_{multiplier:g}x"] = result["daily_returns"].get(day, "")
            writer.writerow(row)


def run_audit():
    snapshot_ms = time.time_ns() // 1_000_000
    end_ms = latest_closed_open_ms(snapshot_ms)
    if end_ms < LISTING_START_MS:
        raise RuntimeError("Current clock precedes the frozen listing boundary")
    expected_coverage = (end_ms - LISTING_START_MS) // HOUR + 1

    sui, sui_meta = fetch_complete_series("SUIUSDT", LISTING_START_MS, end_ms, snapshot_ms)
    btc, btc_meta = fetch_complete_series("BTCUSDT", LISTING_START_MS, end_ms, snapshot_ms)
    sui_times = [c["timestamp"] for c in sui]
    btc_times = [c["timestamp"] for c in btc]
    if sui_times != btc_times:
        raise RuntimeError("SUI and BTC series do not have identical matched timestamps")

    # The previously pinned three-year CSVs are used only as an independent overlap check.
    pinned_sui, pinned_sui_meta = load("SUIUSDT")
    pinned_btc, pinned_btc_meta = load("BTCUSDT")
    cross_validation = {
        "SUIUSDT": compare_overlap(sui, pinned_sui),
        "BTCUSDT": compare_overlap(btc, pinned_btc),
        "method": "Compare every previously pinned hourly OHLCV row against REST data on common timestamps; fail closed on any missing row or mismatch.",
    }
    for symbol in ("SUIUSDT", "BTCUSDT"):
        check = cross_validation[symbol]
        if check["missing_pinned_timestamps"] or check["mismatch_rows"]:
            raise RuntimeError(f"{symbol} pinned data cross-validation failed: {json.dumps(check)}")
        if check["overlap_rows_compared"] != check["pinned_rows"]:
            raise RuntimeError(f"{symbol} did not cross-validate every pinned row")

    if len(sui) != expected_coverage:
        raise RuntimeError(f"Coverage row count mismatch: expected {expected_coverage}, got {len(sui)}")

    # Signals use the exact frozen implementation; no parameters are searched or changed.
    signal_indices = signals(sui)
    raw_results = {}
    summaries = {}
    for multiplier in COST_MULTIPLIERS:
        result = run_causal(
            sui, signal_indices, multiplier,
            start_ts=LISTING_START_MS, end_ts=end_ms,
            exit_lookback=48, initial_capital=CAPITAL,
        )
        raw_results[multiplier] = result
        summaries[str(multiplier).rstrip("0").rstrip(".") + "x"] = summarize_strategy(result, multiplier)

    benchmark_results = {
        "SUI_buy_and_hold": [benchmark_metrics(sui, multiplier) for multiplier in (0.0, 1.0, 2.0, 3.0)],
        "BTC_buy_and_hold": [benchmark_metrics(btc, multiplier) for multiplier in (0.0, 1.0, 2.0, 3.0)],
        "cash": {"net_return": 0.0, "annualized_net_return": 0.0, "assumption": "Uninvested initial capital; no fees."},
    }

    market_path = HERE / "full_history_candles.csv"
    trades_path = HERE / "full_history_trades.csv"
    daily_path = HERE / "full_history_daily_returns.csv"
    write_market_csv(market_path, {"SUIUSDT": sui, "BTCUSDT": btc})
    write_trade_csv(trades_path, raw_results)
    write_daily_csv(daily_path, raw_results)

    report = {
        "schema_version": "expansion-sui-cfg0602-full-history-v1",
        "experiment_id": "HARMONY-BREAKOUT-SUI-CFG0602-FULL-HISTORY-001",
        "status": "FULL_HISTORY_CAUSAL_EVALUATION_COMPLETED_WITH_HOLDOUT_CAVEAT",
        "strategy_conclusion": "HISTORICAL_EXPLORATORY_ONLY; full-history profitability does not prove a durable or independent edge.",
        "generated_utc": iso(snapshot_ms),
        "history": {
            "exchange_market": "Binance Spot SUIUSDT",
            "listing_announcement_url": LISTING_URL,
            "listing_start_utc": iso(LISTING_START_MS),
            "first_observed_candle_utc": iso(sui[0]["timestamp"]),
            "latest_fully_closed_candle_open_utc": iso(end_ms),
            "latest_fully_closed_candle_close_utc": iso(end_ms + HOUR - 1),
            "retrieval_snapshot_utc": iso(snapshot_ms),
            "hourly_rows_per_symbol": len(sui),
            "expected_hourly_rows": expected_coverage,
            "coverage_complete": True,
            "gaps": 0,
            "duplicates": 0,
            "partial_current_candle_excluded": True,
            "ohlcv_validation": "All candles passed monotonic hourly timestamp, finite/positive price, volume, and OHLC-bound checks.",
        },
        "configuration": {
            "asset": "SUIUSDT",
            "timeframe": "1h",
            "config_id": "cfg_0602",
            "content_hash": "f61acc43bbc4f28b",
            "entry_rule": "classic_high_breakout",
            "entry_lookback_bars": 120,
            "atr_period": 14,
            "atr_buffer_multiple": 0.0,
            "confirmation": "two_close",
            "trend_filter": "none",
            "exit": "channel_48",
            "fill": "next_bar_open",
            "initial_capital_quote": CAPITAL,
            "cost_scenarios": SCENARIO_COSTS,
            "no_optimization_or_reselection": True,
        },
        "data_provenance": {
            "SUIUSDT": sui_meta,
            "BTCUSDT": btc_meta,
            "pinned_overlap_cross_validation": cross_validation,
            "previously_pinned_SUI_data": pinned_sui_meta,
            "previously_pinned_BTC_data": pinned_btc_meta,
            "generated_market_csv_sha256": hashlib.sha256(market_path.read_bytes()).hexdigest(),
            "generated_trade_csv_sha256": hashlib.sha256(trades_path.read_bytes()).hexdigest(),
            "generated_daily_returns_csv_sha256": hashlib.sha256(daily_path.read_bytes()).hexdigest(),
        },
        "strategy_results": summaries,
        "benchmarks": benchmark_results,
        "interpretation_and_limitations": [
            "The audit covers the SUIUSDT hourly history from the publicly announced Binance Spot listing hour to the latest fully closed candle observed at retrieval time.",
            "The strategy rules and configuration hash are frozen; only the available date range changed.",
            "All signals are derived from completed candles and orders fill at the next candle open. An open position is liquidated at the final included close with scenario costs.",
            "0x is a gross, zero-fee/zero-slippage reference. 1x/2x/3x use 10/20/30 bps fees and 5/10/15 bps slippage per side.",
            "SUI and BTC benchmarks begin at the same first candle open and end at the same last included candle close, with the same round-trip cost assumptions.",
            "Historical OHLCV simulation does not model market impact, queue position, spread variation, latency, funding, taxes, or capacity constraints.",
            "The original holdout remains EXPOSED_OR_UNKNOWN and the source code-hash scopes remain unreconciled; this full-history replay is not independent holdout evidence.",
            "The complete history includes the previous evaluation interval, so these results are exploratory and potentially affected by prior selection and multiple testing. They are not a deployment approval.",
        ],
        "artifacts": {
            "candles_csv": market_path.name,
            "trades_csv": trades_path.name,
            "daily_returns_csv": daily_path.name,
            "report_json": "full_history_results.json",
        },
    }
    (HERE / "full_history_results.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status": report["status"],
        "history": report["history"],
        "strategy_results": {
            key: {metric: value for metric, value in metrics.items()
                  if metric in ("net_return", "annualized_return", "daily_sharpe", "max_drawdown",
                                "closed_trades", "win_rate", "profit_factor", "exposure_fraction_of_bars",
                                "total_fees_quote", "total_slippage_quote", "turnover_multiple_initial_capital",
                                "top_two_winners_fraction_of_total_net_pnl")}
            for key, metrics in summaries.items()
        },
        "artifacts": report["artifacts"],
    }, indent=2, sort_keys=True))


def main():
    stage = "initialization"
    try:
        stage = "data collection, coverage validation, cross-validation, and full-history simulation"
        run_audit()
    except Exception as exc:
        failure = {
            "schema_version": "expansion-sui-cfg0602-full-history-failure-v1",
            "status": "BLOCKED",
            "stage": stage,
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "listing_start_utc": iso(LISTING_START_MS),
            "error_type": type(exc).__name__,
            "error": str(exc)[:8000],
            "instruction": "Do not interpret the prior limited-window results as this requested full-history result.",
        }
        (HERE / "full_history_failure.json").write_text(
            json.dumps(failure, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(json.dumps(failure, indent=2, sort_keys=True))
        raise


if __name__ == "__main__":
    main()
