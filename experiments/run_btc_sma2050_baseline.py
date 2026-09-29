#!/usr/bin/env python3
"""Run the first fixed-parameter Harmony BTC daily strategy experiment.

Hypothesis:
    A 20-day SMA above a 50-day SMA, evaluated using information available at
    the prior daily close, defines a long-only BTC regime that may reduce the
    effect of prolonged bear markets relative to buy-and-hold.

This is a single preregistered experiment, not an optimization search.
The chronological 70/30 split is fixed before execution. Parameters are not
fit on the OOS segment.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "src")

from harmony_backtest.core import Bar, run_long_only
from harmony_backtest.experiment import DataManifest, ExperimentSpec, StrategySpec


DATASET_ID = "HARMONY-DATA-BTCUSD-DAILY-2014-2025"
STRATEGY_ID = "BTC-SMA20x50-LONGONLY-V1"
EXPERIMENT_ID = "HARMONY-FIN-0001"
FAST_WINDOW = 20
SLOW_WINDOW = 50
OOS_FRACTION = 0.30
FEE_RATE = 0.0006
SLIPPAGE_RATE = 0.0005


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data",
        default="data/cache/btc_usd_historico_diario_2014_2025.csv",
    )
    parser.add_argument(
        "--output-dir",
        default="artifacts/HARMONY-FIN-0001",
    )
    return parser.parse_args()


SPANISH_MONTHS = {
    "ene": 1,
    "feb": 2,
    "mar": 3,
    "abr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dic": 12,
}
ENGLISH_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def parse_source_date(raw: str) -> str:
    parts = raw.strip().lower().split("-")
    if len(parts) != 3:
        raise ValueError(f"Unexpected source date format: {raw!r}")
    day = int(parts[0])
    month_token = parts[1]
    year = 2000 + int(parts[2])
    month = SPANISH_MONTHS.get(month_token) or ENGLISH_MONTHS.get(month_token)
    if month is None:
        raise ValueError(f"Unknown source month token: {month_token!r}")
    return datetime(year, month, day).date().isoformat()


def parse_number(raw: str) -> float:
    value = raw.strip().replace("\\u00ad", "")
    if "," in value and "." in value:
        # The rightmost separator is the decimal marker.
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif value.count(".") > 1:
        # Example: 109.556.16 -> 109556.16 (dot thousands + dot decimal
        # after an earlier naive locale normalization is still reversible).
        parts = value.split(".")
        value = "".join(parts[:-1]) + "." + parts[-1]
    elif "," in value:
        tail = value.rsplit(",", 1)[1]
        if len(tail) in (1, 2):
            value = value.replace(",", ".")
        else:
            value = value.replace(",", "")
    return float(value)


def load_bars(path: Path) -> list[Bar]:
    with path.open("r", newline="", encoding="cp1252") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        if reader.fieldnames is None:
            raise ValueError("CSV has no header")
        required = {"Fecha", "Cierre"}
        missing = required.difference(reader.fieldnames)
        if missing:
            raise ValueError(f"Missing required columns: {sorted(missing)}")

        bars: list[Bar] = []
        for row in reader:
            date = parse_source_date(row["Fecha"])
            close = parse_number(row["Cierre"])
            bars.append(Bar(timestamp=date, close=close))

    bars.sort(key=lambda bar: bar.timestamp)
    for i in range(1, len(bars)):
        if bars[i].timestamp == bars[i - 1].timestamp:
            raise ValueError(
                f"Duplicate calendar date after deterministic normalization: {bars[i].timestamp}"
            )
    return bars


def sma(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    running = 0.0
    for i, value in enumerate(values):
        running += value
        if i >= window:
            running -= values[i - window]
        if i + 1 >= window:
            out[i] = running / window
    return out


def prior_close_signals(bars: list[Bar]) -> list[bool]:
    closes = [bar.close for bar in bars]
    fast = sma(closes, FAST_WINDOW)
    slow = sma(closes, SLOW_WINDOW)
    signals = [False] * len(bars)

    # Decision at t uses only information through t-1, then execution occurs at
    # the current bar close. This prevents current-close look-ahead.
    for i in range(1, len(bars)):
        prev = i - 1
        if fast[prev] is not None and slow[prev] is not None:
            signals[i] = fast[prev] > slow[prev]
    return signals


def split_index(n: int) -> int:
    index = int(math.floor(n * (1.0 - OOS_FRACTION)))
    if index <= SLOW_WINDOW:
        raise ValueError("Dataset is too short for the configured IS/OOS split")
    if index >= n:
        raise ValueError("OOS segment is empty")
    return index


def simulate_equity(
    bars: list[Bar],
    signals: list[bool],
    *,
    fee_rate: float,
    slippage_rate: float,
) -> tuple[list[float], list[float]]:
    cash = 1.0
    units = 0.0
    in_position = False
    equity_curve: list[float] = []
    daily_returns: list[float] = []
    previous_equity = 1.0

    for i, (bar, desired_long) in enumerate(zip(bars, signals)):
        if desired_long and not in_position:
            execution_price = bar.close * (1.0 + slippage_rate)
            investable = cash * (1.0 - fee_rate)
            units = investable / execution_price
            cash = 0.0
            in_position = True
        elif not desired_long and in_position:
            execution_price = bar.close * (1.0 - slippage_rate)
            cash = units * execution_price * (1.0 - fee_rate)
            units = 0.0
            in_position = False

        # Match the core engine: any remaining position is liquidated at the
        # final bar, with exit slippage and fee.
        if i == len(bars) - 1 and in_position:
            execution_price = bar.close * (1.0 - slippage_rate)
            cash = units * execution_price * (1.0 - fee_rate)
            units = 0.0
            in_position = False

        equity = cash + units * bar.close
        equity_curve.append(equity)
        daily_returns.append(equity / previous_equity - 1.0)
        previous_equity = equity

    return equity_curve, daily_returns


def metric_summary(
    bars: list[Bar],
    equity_curve: list[float],
    daily_returns: list[float],
    trade_returns: list[float],
) -> dict[str, float | int | str]:
    final_equity = equity_curve[-1]
    start_date = datetime.fromisoformat(bars[0].timestamp)
    end_date = datetime.fromisoformat(bars[-1].timestamp)
    days = max(1, (end_date - start_date).days)

    cagr = final_equity ** (365.25 / days) - 1.0
    peak = equity_curve[0]
    max_drawdown = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - 1.0)

    returns = daily_returns[1:] if len(daily_returns) > 1 else []
    if len(returns) >= 2:
        stdev = statistics.stdev(returns)
        sharpe = (statistics.mean(returns) / stdev) * math.sqrt(365.0) if stdev else 0.0
    else:
        sharpe = 0.0

    gains = sum(r for r in trade_returns if r > 0)
    losses = -sum(r for r in trade_returns if r < 0)
    profit_factor = gains / losses if losses else float("inf") if gains else 0.0

    return {
        "start": bars[0].timestamp,
        "end": bars[-1].timestamp,
        "final_equity": final_equity,
        "cumulative_return": final_equity - 1.0,
        "cagr": cagr,
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
        "trade_count": len(trade_returns),
        "profit_factor": profit_factor,
        "mean_net_trade_return": statistics.mean(trade_returns) if trade_returns else 0.0,
    }


def main() -> int:
    args = parse_args()
    data_path = Path(args.data)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    bars = load_bars(data_path)
    if len(bars) != 4063:
        raise ValueError(f"Unexpected row count: expected 4063, got {len(bars)}")

    data_sha256 = os.environ.get(
        "HARMONY_DATA_SHA256",
        "7bfbbcabb92b91898b67ed38bae71f4e50f2a37c636762703a3b9af08968343b",
    )
    data_manifest = DataManifest(
        source="Zenodo",
        symbol="BTC-USD",
        timeframe="1d",
        start=bars[0].timestamp,
        end=bars[-1].timestamp,
        source_locator="10.5281/zenodo.17566064",
        content_sha256=data_sha256,
        row_count=len(bars),
    )

    strategy = StrategySpec(
        strategy_id=STRATEGY_ID,
        name="BTC daily SMA 20/50 long-only",
        parameters={
            "fast_window": FAST_WINDOW,
            "slow_window": SLOW_WINDOW,
            "signal_lag_bars": 1,
            "oos_fraction": OOS_FRACTION,
        },
        signal_rule="LONG iff SMA20(t-1) > SMA50(t-1); otherwise flat.",
        execution_rule="Execute at current daily close with explicit fee and slippage; flatten on final bar.",
        fee_rate=FEE_RATE,
        slippage_rate=SLIPPAGE_RATE,
    )

    code_revision = os.environ.get("GITHUB_SHA", "local")
    experiment = ExperimentSpec(
        experiment_id=EXPERIMENT_ID,
        research_scope="fixed BTC daily SMA20/SMA50 regime baseline; chronological 70/30 split",
        data_manifest_id=data_manifest.manifest_id,
        strategy_digest=strategy.strategy_digest,
        validation_method="chronological_70_30_oos_fixed_parameters",
        code_revision=code_revision,
    )

    signals = prior_close_signals(bars)
    split = split_index(len(bars))

    oos_bars = bars[split:]
    oos_signals = signals[split:]
    strategy_core = run_long_only(
        bars,
        [False] * split + oos_signals,
        fee_rate=FEE_RATE,
        slippage_rate=SLIPPAGE_RATE,
    )
    strategy_curve, strategy_daily_returns = simulate_equity(
        oos_bars,
        oos_signals,
        fee_rate=FEE_RATE,
        slippage_rate=SLIPPAGE_RATE,
    )
    if abs(strategy_curve[-1] - strategy_core.final_cash) > 1e-12:
        raise AssertionError("Independent equity simulation disagrees with core engine")

    benchmark_signals = [True] * len(oos_bars)
    benchmark_core = run_long_only(
        oos_bars,
        benchmark_signals,
        fee_rate=FEE_RATE,
        slippage_rate=SLIPPAGE_RATE,
    )
    benchmark_curve, benchmark_daily_returns = simulate_equity(
        oos_bars,
        benchmark_signals,
        fee_rate=FEE_RATE,
        slippage_rate=SLIPPAGE_RATE,
    )
    if abs(benchmark_curve[-1] - benchmark_core.final_cash) > 1e-12:
        raise AssertionError("Benchmark equity simulation disagrees with core engine")

    result = {
        "experiment": {
            "experiment_id": experiment.experiment_id,
            "experiment_digest": experiment.experiment_digest,
            "strategy_id": strategy.strategy_id,
            "strategy_digest": strategy.strategy_digest,
            "data_manifest_id": data_manifest.manifest_id,
            "code_revision": code_revision,
            "validation_method": experiment.validation_method,
        },
        "data": {
            "dataset_id": DATASET_ID,
            "sha256": data_sha256,
            "row_count": len(bars),
            "coverage": [bars[0].timestamp, bars[-1].timestamp],
            "encoding": "cp1252",
        },
        "configuration": {
            "fast_window": FAST_WINDOW,
            "slow_window": SLOW_WINDOW,
            "oos_fraction": OOS_FRACTION,
            "fee_rate": FEE_RATE,
            "slippage_rate": SLIPPAGE_RATE,
            "split_index": split,
            "split_date": oos_bars[0].timestamp,
        },
        "oos_strategy": metric_summary(
            oos_bars,
            strategy_curve,
            strategy_daily_returns,
            [trade.net_return for trade in strategy_core.trades],
        ),
        "oos_buy_and_hold": metric_summary(
            oos_bars,
            benchmark_curve,
            benchmark_daily_returns,
            [trade.net_return for trade in benchmark_core.trades],
        ),
        "comparison": {
            "strategy_minus_buy_hold_cumulative_return": (
                strategy_core.final_cash - benchmark_core.final_cash
            ),
        },
        "integrity_checks": {
            "current_close_lookahead": False,
            "parameters_fit_on_oos": False,
            "explicit_costs": True,
            "core_engine_matches_independent_equity_simulation": True,
        },
    }

    identity = {
        "data_manifest": asdict(data_manifest),
        "strategy_spec": asdict(strategy),
        "experiment_spec": asdict(experiment),
    }

    (output_dir / "identity.json").write_text(
        json.dumps(identity, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (output_dir / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
