#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

SYMBOL = "SOXL"
INTERVAL = "1h"
LOOKBACKS = (30, 90, 365)
START_UTC = datetime(2024, 10, 18, 0, 0, tzinfo=timezone.utc)
END_UTC = datetime(2026, 10, 7, 0, 0, tzinfo=timezone.utc)
PRIMARY_FEE = 0.0003
PRIMARY_SLIPPAGE = 0.0002
COST_STRESSES = (1.0, 1.5, 2.0)
INITIAL_EQUITY = 1.0
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "harmony_soxl_intraday_horizon_001"


@dataclass(frozen=True)
class BacktestMetrics:
    total_return: float
    cagr: float
    sharpe: float
    max_drawdown: float
    turnover: float
    trades: int
    costs: float


def yahoo_chart_url(symbol: str, start: datetime, end: datetime) -> str:
    query = urllib.parse.urlencode(
        {
            "period1": int(start.timestamp()),
            "period2": int(end.timestamp()),
            "interval": INTERVAL,
            "includePrePost": "false",
            "events": "div,splits",
            "lang": "en-US",
            "region": "US",
        }
    )
    return f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{query}"


def fetch_data() -> tuple[pd.DataFrame, dict, str]:
    url = yahoo_chart_url(SYMBOL, START_UTC, END_UTC)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 harmony-soxl-intraday-horizon-001",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload_bytes = response.read()
    sha = hashlib.sha256(payload_bytes).hexdigest()
    payload = json.loads(payload_bytes.decode("utf-8"))
    chart = payload["chart"]
    if chart.get("error"):
        raise RuntimeError(f"Yahoo chart error: {chart['error']}")
    result = chart["result"][0]
    timestamps = result.get("timestamp") or []
    quote = result["indicators"]["quote"][0]
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(timestamps, unit="s", utc=True),
            "open": quote.get("open"),
            "high": quote.get("high"),
            "low": quote.get("low"),
            "close": quote.get("close"),
            "volume": quote.get("volume"),
        }
    )
    frame = frame.dropna(subset=["timestamp", "open", "high", "low", "close"]).copy()
    frame = frame.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    frame["volume"] = frame["volume"].fillna(0.0).astype(float)
    for col in ("open", "high", "low", "close"):
        frame[col] = frame[col].astype(float)

    if frame.empty:
        raise RuntimeError("Yahoo returned no usable bars.")
    if not frame["timestamp"].is_monotonic_increasing:
        raise AssertionError("timestamps are not monotonic increasing")
    if frame["timestamp"].duplicated().any():
        raise AssertionError("duplicate timestamps detected")
    if (frame[["open", "high", "low", "close"]] <= 0).any().any():
        raise AssertionError("non-positive prices detected")
    if (frame["high"] < frame[["open", "close", "low"]].max(axis=1)).any():
        raise AssertionError("high violates OHLC bounds")
    if (frame["low"] > frame[["open", "close", "high"]].min(axis=1)).any():
        raise AssertionError("low violates OHLC bounds")

    meta = {
        "currency": result.get("meta", {}).get("currency"),
        "exchangeTimezoneName": result.get("meta", {}).get("exchangeTimezoneName"),
        "instrumentType": result.get("meta", {}).get("instrumentType"),
        "exchangeName": result.get("meta", {}).get("exchangeName"),
        "events": result.get("events", {}),
        "url": url,
    }
    return frame, meta, sha


def horizon_exposure(closes: pd.Series, t: int) -> float:
    if t < max(LOOKBACKS):
        return 0.0
    prior_close = float(closes.iloc[t - 1])
    positives = sum(
        prior_close > float(closes.iloc[t - 1 - lookback])
        for lookback in LOOKBACKS
    )
    if positives == 3:
        return 1.0
    if positives == 2:
        return 2.0 / 3.0
    return 0.0


def exposures(frame: pd.DataFrame) -> pd.Series:
    return pd.Series(
        [horizon_exposure(frame["close"], i) for i in range(len(frame))],
        index=frame.index,
        dtype=float,
    )


def annualization_factor(frame: pd.DataFrame) -> float:
    bars_per_day = frame.groupby(frame["timestamp"].dt.date).size()
    typical = float(bars_per_day.median())
    if typical <= 0:
        raise RuntimeError("could not infer intraday bar count")
    return math.sqrt(252.0 * typical)


def metrics_from_curve(
    curve: pd.DataFrame,
    annualization: float,
    costs: float,
    trades: int,
) -> BacktestMetrics:
    equity = curve["equity"]
    returns = curve["period_return"]
    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    elapsed_days = max(
        (curve["timestamp"].iloc[-1] - curve["timestamp"].iloc[0]).total_seconds() / 86400.0,
        1.0,
    )
    cagr = float((equity.iloc[-1] / equity.iloc[0]) ** (365.25 / elapsed_days) - 1.0)
    std = float(returns.std(ddof=1))
    sharpe = float((returns.mean() / std) * annualization) if std > 0 else 0.0
    running_max = equity.cummax()
    max_drawdown = float((equity / running_max - 1.0).min())
    turnover = float(curve["trade_notional_fraction"].sum())
    return BacktestMetrics(
        total_return=total_return,
        cagr=cagr,
        sharpe=sharpe,
        max_drawdown=max_drawdown,
        turnover=turnover,
        trades=trades,
        costs=costs,
    )


def run_strategy(
    frame: pd.DataFrame,
    fee_rate: float,
    slippage_rate: float,
    split_index: int,
) -> dict:
    target = exposures(frame)
    equity = INITIAL_EQUITY
    previous_target = 0.0
    prev_close = float(frame.iloc[0]["close"])
    rows = []
    costs_total = 0.0
    trade_count = 0

    for i, row in frame.iterrows():
        open_price = float(row["open"])
        close_price = float(row["close"])
        current_target = float(target.iloc[i])

        overnight_return = (
            previous_target * (open_price / prev_close - 1.0) if i > 0 else 0.0
        )

        traded = abs(current_target - previous_target)
        trade_notional = equity * traded
        trading_cost = trade_notional * (fee_rate + slippage_rate)
        if traded > 1e-12:
            trade_count += 1

        intrabar_return = current_target * (close_price / open_price - 1.0)
        period_return = overnight_return + intrabar_return
        equity = equity * (1.0 + period_return) - trading_cost
        costs_total += trading_cost

        rows.append(
            {
                "timestamp": row["timestamp"],
                "open": open_price,
                "close": close_price,
                "target_exposure": current_target,
                "previous_exposure": previous_target,
                "overnight_return": overnight_return,
                "intrabar_return": intrabar_return,
                "period_return": period_return,
                "trade_notional_fraction": traded,
                "trading_cost": trading_cost,
                "equity": equity,
                "in_oos": bool(i >= split_index),
            }
        )

        previous_target = current_target
        prev_close = close_price

    curve = pd.DataFrame(rows)
    annual = annualization_factor(frame)
    full = metrics_from_curve(curve, annual, costs_total, trade_count)

    oos = curve.iloc[split_index:].copy()
    oos_start_equity = float(curve.iloc[split_index - 1]["equity"]) if split_index > 0 else 1.0
    oos_curve = oos.copy()
    oos_curve["equity"] = oos_curve["equity"] / oos_start_equity
    oos_trade_count = int((oos_curve["target_exposure"].diff().abs() > 1e-12).sum())
    oos_costs = float(oos_curve["trading_cost"].sum())
    oos_metrics = metrics_from_curve(oos_curve, annual, oos_costs, oos_trade_count)

    bh_equity = [INITIAL_EQUITY]
    for i in range(1, len(frame)):
        bh_equity.append(
            bh_equity[-1] * (float(frame.iloc[i]["close"]) / float(frame.iloc[i - 1]["close"]))
        )
    bh = curve.copy()
    bh["equity"] = bh_equity
    bh["period_return"] = bh["equity"].pct_change().fillna(0.0)
    bh_metrics = metrics_from_curve(bh, annual, 0.0, 1)

    return {
        "curve": curve,
        "full": full,
        "oos": oos_metrics,
        "buy_and_hold": bh_metrics,
        "buy_and_hold_curve": bh,
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    frame, meta, data_sha = fetch_data()
    split_index = int(len(frame) * 0.70)
    if split_index <= max(LOOKBACKS) + 10:
        raise RuntimeError("insufficient history for frozen horizons plus OOS split")

    frame.to_csv(OUT / "SOXL_1h.csv", index=False)

    primary = run_strategy(frame, PRIMARY_FEE, PRIMARY_SLIPPAGE, split_index)
    exposure_series = exposures(frame)

    result = {
        "experiment_id": "HARMONY-FIN-0017",
        "status": "EXECUTED",
        "instrument": SYMBOL,
        "interval": INTERVAL,
        "data": {
            "source": "Yahoo Finance chart API",
            "start": frame["timestamp"].iloc[0].isoformat(),
            "end": frame["timestamp"].iloc[-1].isoformat(),
            "requested_start": START_UTC.isoformat(),
            "requested_end": END_UTC.isoformat(),
            "rows": int(len(frame)),
            "content_sha256": data_sha,
            "meta": meta,
        },
        "signal": {
            "lookbacks_bars": LOOKBACKS,
            "vote_rule": "3 positive -> 1.0; 2 positive -> 2/3; else 0",
            "long_only": True,
            "effective_exposure_mean": float(exposure_series.mean()),
        },
        "split": {
            "is_fraction": 0.70,
            "oos_fraction": 0.30,
            "split_index": split_index,
            "oos_start": frame["timestamp"].iloc[split_index].isoformat(),
        },
        "costs": {
            "fee_rate": PRIMARY_FEE,
            "slippage_rate": PRIMARY_SLIPPAGE,
            "combined_one_way_rate": PRIMARY_FEE + PRIMARY_SLIPPAGE,
            "stress_multipliers": COST_STRESSES,
        },
        "full_sample": primary["full"].__dict__,
        "oos": primary["oos"].__dict__,
        "benchmark_buy_and_hold": primary["buy_and_hold"].__dict__,
    }

    stress = {}
    for multiplier in COST_STRESSES:
        run = run_strategy(
            frame,
            PRIMARY_FEE * multiplier,
            PRIMARY_SLIPPAGE * multiplier,
            split_index,
        )
        stress[str(multiplier)] = {
            "oos": run["oos"].__dict__,
            "full": run["full"].__dict__,
        }
    result["cost_stress"] = stress

    primary["curve"].to_csv(OUT / "strategy_curve.csv", index=False)
    primary["buy_and_hold_curve"].to_csv(OUT / "buy_and_hold_curve.csv", index=False)
    (OUT / "results.json").write_text(json.dumps(result, indent=2, default=str))
    (OUT / "strategy_spec.yaml").write_text(
        (ROOT / "experiments" / "HARMONY-FIN-0017.yaml").read_text()
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
