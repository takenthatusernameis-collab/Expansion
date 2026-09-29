#!/usr/bin/env python3
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
STRATEGY_ID = "BTC-MOM20-SIGN-LONGONLY-V1"
EXPERIMENT_ID = "HARMONY-FIN-0002"
LOOKBACK = 20
OOS_FRACTION = 0.30
FEE_RATE = 0.0006
SLIPPAGE_RATE = 0.0005

SPANISH_MONTHS = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
}

def parse_source_date(raw: str) -> str:
    parts = raw.strip().lower().split("-")
    if len(parts) != 3:
        raise ValueError(f"Unexpected source date format: {raw!r}")
    day = int(parts[0])
    year = 2000 + int(parts[2])
    month = SPANISH_MONTHS.get(parts[1])
    if month is None:
        raise ValueError(f"Unknown source month token: {parts[1]!r}")
    return datetime(year, month, day).date().isoformat()

def parse_number(raw: str) -> float:
    value = raw.strip().replace("\u00ad", "")
    if "," in value and "." in value:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif value.count(".") > 1:
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
        if not reader.fieldnames:
            raise ValueError("CSV has no header")
        if "Fecha" not in reader.fieldnames or "Cierre" not in reader.fieldnames:
            raise ValueError("CSV missing Fecha/Cierre")
        bars = [
            Bar(parse_source_date(row["Fecha"]), parse_number(row["Cierre"]))
            for row in reader
        ]
    bars.sort(key=lambda b: b.timestamp)
    for i in range(1, len(bars)):
        if bars[i].timestamp == bars[i - 1].timestamp:
            raise ValueError(f"Duplicate date: {bars[i].timestamp}")
    return bars

def momentum_signals(bars: list[Bar]) -> list[bool]:
    closes = [b.close for b in bars]
    signals = [False] * len(bars)
    for i in range(1, len(bars)):
        anchor = i - 1 - LOOKBACK
        if anchor >= 0:
            signals[i] = closes[i - 1] > closes[anchor]
    return signals

def simulate_equity(
    bars: list[Bar],
    signals: list[bool],
    *,
    fee_rate: float,
    slippage_rate: float,
) -> tuple[list[float], list[float]]:
    cash, units = 1.0, 0.0
    in_position = False
    curve, returns = [], []
    previous = 1.0
    for i, (bar, desired) in enumerate(zip(bars, signals)):
        if desired and not in_position:
            px = bar.close * (1.0 + slippage_rate)
            units = cash * (1.0 - fee_rate) / px
            cash = 0.0
            in_position = True
        elif not desired and in_position:
            px = bar.close * (1.0 - slippage_rate)
            cash = units * px * (1.0 - fee_rate)
            units = 0.0
            in_position = False
        if i == len(bars) - 1 and in_position:
            px = bar.close * (1.0 - slippage_rate)
            cash = units * px * (1.0 - fee_rate)
            units = 0.0
            in_position = False
        equity = cash + units * bar.close
        curve.append(equity)
        returns.append(equity / previous - 1.0)
        previous = equity
    return curve, returns

def metrics(bars, curve, daily_returns, trade_returns):
    final = curve[-1]
    days = max(1, (datetime.fromisoformat(bars[-1].timestamp) -
                   datetime.fromisoformat(bars[0].timestamp)).days)
    cagr = final ** (365.25 / days) - 1.0
    peak, mdd = curve[0], 0.0
    for x in curve:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1.0)
    r = daily_returns[1:]
    stdev = statistics.stdev(r) if len(r) >= 2 else 0.0
    sharpe = (statistics.mean(r) / stdev) * math.sqrt(365.0) if stdev else 0.0
    gains = sum(x for x in trade_returns if x > 0)
    losses = -sum(x for x in trade_returns if x < 0)
    pf = gains / losses if losses else (float("inf") if gains else 0.0)
    return {
        "start": bars[0].timestamp,
        "end": bars[-1].timestamp,
        "final_equity": final,
        "cumulative_return": final - 1.0,
        "cagr": cagr,
        "max_drawdown": mdd,
        "sharpe": sharpe,
        "trade_count": len(trade_returns),
        "profit_factor": pf,
        "mean_net_trade_return": statistics.mean(trade_returns) if trade_returns else 0.0,
    }

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/cache/btc_usd_historico_diario_2014_2025.csv")
    ap.add_argument("--output-dir", default="artifacts/HARMONY-FIN-0002")
    args = ap.parse_args()

    path = Path(args.data)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    bars = load_bars(path)
    if len(bars) != 4063:
        raise ValueError(f"Unexpected row count: {len(bars)}")

    data_sha = os.environ.get(
        "HARMONY_DATA_SHA256",
        "7bfbbcabb92b91898b67ed38bae71f4e50f2a37c636762703a3b9af08968343b",
    )
    manifest = DataManifest(
        source="Zenodo",
        symbol="BTC-USD",
        timeframe="1d",
        start=bars[0].timestamp,
        end=bars[-1].timestamp,
        source_locator="10.5281/zenodo.17566064",
        content_sha256=data_sha,
        row_count=len(bars),
    )
    strategy = StrategySpec(
        strategy_id=STRATEGY_ID,
        name="BTC daily 20-day time-series momentum sign",
        parameters={"lookback": LOOKBACK, "signal_lag_bars": 1, "oos_fraction": OOS_FRACTION},
        signal_rule="LONG iff close(t-1) > close(t-1-20); otherwise flat.",
        execution_rule="Execute at current daily close with explicit fee and slippage; flatten on final bar.",
        fee_rate=FEE_RATE,
        slippage_rate=SLIPPAGE_RATE,
    )
    experiment = ExperimentSpec(
        experiment_id=EXPERIMENT_ID,
        research_scope="fixed BTC daily 20-day momentum sign; chronological 70/30 split",
        data_manifest_id=manifest.manifest_id,
        strategy_digest=strategy.strategy_digest,
        validation_method="chronological_70_30_oos_fixed_parameters",
        code_revision=os.environ.get("GITHUB_SHA", "local"),
    )

    signals = momentum_signals(bars)
    split = int(math.floor(len(bars) * (1.0 - OOS_FRACTION)))
    oos_bars, oos_signals = bars[split:], signals[split:]

    core = run_long_only(
        oos_bars, oos_signals,
        fee_rate=FEE_RATE, slippage_rate=SLIPPAGE_RATE
    )
    curve, daily = simulate_equity(
        oos_bars, oos_signals,
        fee_rate=FEE_RATE, slippage_rate=SLIPPAGE_RATE
    )
    if abs(core.final_cash - curve[-1]) > 1e-12:
        raise AssertionError("Core engine mismatch")

    bh_signals = [True] * len(oos_bars)
    bh = run_long_only(
        oos_bars, bh_signals,
        fee_rate=FEE_RATE, slippage_rate=SLIPPAGE_RATE
    )
    bh_curve, bh_daily = simulate_equity(
        oos_bars, bh_signals,
        fee_rate=FEE_RATE, slippage_rate=SLIPPAGE_RATE
    )
    if abs(bh.final_cash - bh_curve[-1]) > 1e-12:
        raise AssertionError("Benchmark core engine mismatch")

    result = {
        "experiment": {
            "experiment_id": experiment.experiment_id,
            "experiment_digest": experiment.experiment_digest,
            "strategy_id": strategy.strategy_id,
            "strategy_digest": strategy.strategy_digest,
            "data_manifest_id": manifest.manifest_id,
            "code_revision": experiment.code_revision,
            "validation_method": experiment.validation_method,
        },
        "data": {
            "dataset_id": DATASET_ID,
            "sha256": data_sha,
            "row_count": len(bars),
            "coverage": [bars[0].timestamp, bars[-1].timestamp],
            "encoding": "cp1252",
        },
        "configuration": {
            "lookback": LOOKBACK,
            "signal_lag_bars": 1,
            "oos_fraction": OOS_FRACTION,
            "split_index": split,
            "split_date": oos_bars[0].timestamp,
            "fee_rate": FEE_RATE,
            "slippage_rate": SLIPPAGE_RATE,
        },
        "oos_strategy": metrics(
            oos_bars, curve, daily, [t.net_return for t in core.trades]
        ),
        "oos_buy_and_hold": metrics(
            oos_bars, bh_curve, bh_daily, [t.net_return for t in bh.trades]
        ),
        "comparison": {
            "strategy_minus_buy_hold_cumulative_return":
                core.final_cash - bh.final_cash
        },
        "integrity_checks": {
            "current_close_lookahead": False,
            "parameters_fit_on_oos": False,
            "explicit_costs": True,
            "core_engine_matches_independent_equity_simulation": True,
        },
    }

    identity = {
        "data_manifest": asdict(manifest),
        "strategy_spec": asdict(strategy),
        "experiment_spec": asdict(experiment),
    }
    (out / "identity.json").write_text(
        json.dumps(identity, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (out / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
