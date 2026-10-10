#!/usr/bin/env python3
"""Frozen, no-live-trading PEPEUSDT.P NYSE-session research backtest."""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd
import pandas_market_calendars as mcal

EXPERIMENT_ID = "HARMONY-PEPE-SESSION-002"
INSTRUMENT_ID = "PEPE-USDT-SWAP"
BASE_URL = "https://www.okx.com"
START_MS = int(pd.Timestamp("2023-01-01T00:00:00Z").timestamp() * 1000)
BAR_MS = 5 * 60 * 1000
NY = "America/New_York"
TAKER_FEE_BPS = 5.0
SLIPPAGE_BPS = {"BASE": 5.0, "STRESS_2X_SLIPPAGE": 10.0, "STRESS_4X_SLIPPAGE": 20.0}
N_BOOTSTRAPS = 1000
BOOTSTRAP_BLOCK = 5
MIN_FINAL_OOS_TRADES = 100
MIN_OOS_SHARPE = 0.9
MIN_FUNDING_COVERAGE = 0.95


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def get_json(path: str, params: dict, retries: int = 6) -> dict:
    url = BASE_URL + path + "?" + urlencode(params)
    last_error = None
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": "HARMONY-research/1.0", "Accept": "application/json"})
            with urlopen(req, timeout=30) as response:
                payload = response.read()
            obj = json.loads(payload.decode("utf-8"))
            if not isinstance(obj, dict) or obj.get("code", "0") != "0":
                raise RuntimeError("OKX API error: " + str(obj.get("code")) + " " + str(obj.get("msg")))
            return obj
        except Exception as exc:
            last_error = exc
            time.sleep(min(2.0 ** attempt, 20.0))
    raise RuntimeError(f"request failed after {retries} attempts: {url}; error={last_error}")


def fetch_candles() -> tuple[pd.DataFrame, dict]:
    rows = []
    cursor = None
    pages = 0
    seen_oldest = None
    start_time = time.time()
    while pages < 2200:
        params = {"instId": INSTRUMENT_ID, "bar": "5m", "limit": "300"}
        if cursor is not None:
            params["after"] = str(cursor)
        payload = get_json("/api/v5/market/history-candles", params)
        page = payload.get("data", [])
        pages += 1
        if not page:
            break
        for r in page:
            if len(r) < 9:
                continue
            ts = int(r[0])
            if ts < START_MS or str(r[8]) != "1":
                continue
            rows.append({
                "ts_ms": ts, "open": float(r[1]), "high": float(r[2]), "low": float(r[3]),
                "close": float(r[4]), "vol": float(r[5]), "volCcy": float(r[6]),
                "volCcyQuote": float(r[7]), "confirm": int(r[8])
            })
        timestamps = [int(r[0]) for r in page if len(r) >= 1]
        if not timestamps:
            break
        oldest = min(timestamps)
        if oldest <= START_MS:
            break
        if seen_oldest is not None and oldest >= seen_oldest:
            raise RuntimeError(f"candle pagination did not move backwards: prior={seen_oldest}, current={oldest}")
        seen_oldest = oldest
        cursor = oldest - 1
        time.sleep(0.115)  # Below OKX's documented 20 requests per 2 seconds.

    if not rows:
        raise RuntimeError("OKX returned no confirmed PEPE-USDT-SWAP 5m candles.")
    df = pd.DataFrame(rows).drop_duplicates(subset=["ts_ms"]).sort_values("ts_ms").reset_index(drop=True)
    numeric = ["open", "high", "low", "close", "vol", "volCcy", "volCcyQuote"]
    if not np.isfinite(df[numeric].to_numpy(dtype=float)).all():
        raise RuntimeError("Non-finite numeric candle values detected.")
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        raise RuntimeError("Non-positive OHLC price detected.")
    if (df["high"] < df["low"]).any():
        raise RuntimeError("High < low detected.")
    info = {
        "pages_requested": pages,
        "row_count": int(len(df)),
        "first_candle_utc": pd.to_datetime(int(df.ts_ms.min()), unit="ms", utc=True).isoformat(),
        "last_confirmed_candle_utc": pd.to_datetime(int(df.ts_ms.max()), unit="ms", utc=True).isoformat(),
        "download_elapsed_seconds": round(time.time() - start_time, 2),
        "duplicates_removed": int(len(rows) - len(df)),
    }
    return df, info


def fetch_funding() -> tuple[pd.DataFrame, dict]:
    """OKX documents that public funding-rate history covers up to three months."""
    records = []
    cursor = None
    pages = 0
    try:
        while pages < 12:
            params = {"instId": INSTRUMENT_ID, "limit": "400"}
            if cursor is not None:
                params["after"] = str(cursor)
            payload = get_json("/api/v5/public/funding-rate-history", params)
            page = payload.get("data", [])
            pages += 1
            if not page:
                break
            for r in page:
                ts = int(r["fundingTime"])
                val = r.get("realizedRate") or r.get("fundingRate")
                if val not in (None, ""):
                    records.append({
                        "funding_ts_ms": ts,
                        "funding_rate": float(val),
                        "funding_rate_predicted_field": r.get("fundingRate", ""),
                        "realized_rate_field": r.get("realizedRate", ""),
                    })
            oldest = min(int(r["fundingTime"]) for r in page)
            if oldest <= START_MS:
                break
            if cursor is not None and oldest >= cursor:
                break
            if len(page) < 400:
                break
            cursor = oldest - 1
            time.sleep(0.22)  # Funding endpoint limit: 10 requests per 2 seconds.
        f = pd.DataFrame(records)
        if f.empty:
            return f, {"status": "UNAVAILABLE_OR_EMPTY", "pages_requested": pages, "row_count": 0,
                       "coverage_start_utc": None, "coverage_end_utc": None,
                       "coverage_limit_note": "OKX documents up to three months of funding history."}
        f = f.drop_duplicates("funding_ts_ms").sort_values("funding_ts_ms").reset_index(drop=True)
        return f, {
            "status": "PARTIAL_HISTORY_AVAILABLE", "pages_requested": pages, "row_count": int(len(f)),
            "coverage_start_utc": pd.to_datetime(int(f.funding_ts_ms.min()), unit="ms", utc=True).isoformat(),
            "coverage_end_utc": pd.to_datetime(int(f.funding_ts_ms.max()), unit="ms", utc=True).isoformat(),
            "coverage_limit_note": "OKX documents that this endpoint returns funding history for up to three months; older sessions cannot be fully funding-adjusted from this source."
        }
    except Exception as exc:
        return pd.DataFrame(columns=["funding_ts_ms", "funding_rate", "funding_rate_predicted_field", "realized_rate_field"]), {
            "status": "FETCH_FAILED", "pages_requested": pages, "row_count": 0,
            "coverage_start_utc": None, "coverage_end_utc": None, "error": str(exc),
            "coverage_limit_note": "Funding history unavailable; all candidate results are non-promotable."
        }


def canonical_csv_bytes(df: pd.DataFrame, columns: list[str]) -> bytes:
    return df[columns].to_csv(index=False, lineterminator="\n", float_format="%.12g").encode("utf-8")


def save_gzip_deterministic(path: Path, payload: bytes) -> None:
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            gz.write(payload)


def market_sessions(bars: pd.DataFrame) -> tuple[list[tuple[str, pd.DataFrame]], dict]:
    df = bars.copy()
    utc = pd.to_datetime(df.ts_ms, unit="ms", utc=True)
    local = utc.dt.tz_convert(NY)
    df["ts_utc"] = utc
    df["local_date"] = local.dt.strftime("%Y-%m-%d")
    minute = local.dt.hour * 60 + local.dt.minute
    df["local_minute"] = minute
    df["local_weekday"] = local.dt.weekday

    calendar = mcal.get_calendar("NYSE")
    schedule = calendar.schedule(start_date=df.local_date.min(), end_date=df.local_date.max())
    regular_dates = set()
    early_close_dates = set()
    for sched_date, row in schedule.iterrows():
        date = pd.Timestamp(sched_date).strftime("%Y-%m-%d")
        market_open_local = pd.Timestamp(row["market_open"]).tz_convert(NY)
        market_close_local = pd.Timestamp(row["market_close"]).tz_convert(NY)
        is_regular_open = (market_open_local.hour, market_open_local.minute) == (9, 30)
        is_regular_close = (market_close_local.hour, market_close_local.minute) == (16, 0)
        if is_regular_open and is_regular_close:
            regular_dates.add(date)
        else:
            early_close_dates.add(date)

    session_window = (df.local_minute >= 570) & (df.local_minute < 960) & (df.local_weekday < 5)
    observed_window = df[session_window].copy()
    observed_dates = set(observed_window.local_date.astype(str))
    eligible = df[
        df.local_date.isin(regular_dates) & (df.local_weekday < 5)
        & (df.local_minute >= 570) & (df.local_minute < 960)
        & (((df.local_minute - 570) % 5) == 0)
    ].copy()

    expected_minutes = list(range(570, 960, 5))
    kept = []
    excluded = {}
    for date in sorted(early_close_dates & observed_dates):
        count = int((observed_window.local_date == date).sum())
        excluded[date] = {
            "observed_bars": count,
            "expected_bars": 78,
            "reason": "NYSE early-close session excluded by scheduled market_close"
        }
    for date, g in eligible.groupby("local_date", sort=True):
        g = g.sort_values("ts_ms").drop_duplicates("ts_ms")
        got = g.local_minute.astype(int).tolist()
        if len(g) == 78 and got == expected_minutes:
            kept.append((date, g.reset_index(drop=True)))
        else:
            excluded[date] = {
                "observed_bars": int(len(g)),
                "expected_bars": 78,
                "reason": "incomplete regular session"
            }
    return kept, {
        "nyse_calendar_name": "NYSE",
        "timezone": NY,
        "session_start_local": "09:30",
        "session_end_local": "16:00",
        "expected_bars_per_complete_session": 78,
        "eligible_observed_dates": int(eligible.local_date.nunique()),
        "complete_sessions": int(len(kept)),
        "early_close_dates_detected": int(len(early_close_dates & observed_dates)),
        "incomplete_or_early_close_sessions_excluded": int(len(excluded)),
        "excluded_session_examples": dict(list(sorted(excluded.items()))[:30]),
    }

def compute_signals(g: pd.DataFrame, strategy: str) -> np.ndarray:
    close = g.close.to_numpy(dtype=float)
    high = g.high.to_numpy(dtype=float)
    low = g.low.to_numpy(dtype=float)
    volume = g.volCcy.to_numpy(dtype=float)
    fallback = g.vol.to_numpy(dtype=float)
    volume = np.where(np.isfinite(volume) & (volume > 0), volume, fallback)
    volume = np.where(np.isfinite(volume) & (volume > 0), volume, 1.0)
    typical = (high + low + close) / 3.0
    vwap = np.cumsum(typical * volume) / np.maximum(np.cumsum(volume), 1e-12)
    n = len(g)
    signal = np.zeros(n, dtype=int)

    if strategy == "ORB30":
        range_high = float(np.max(high[:6]))
        range_low = float(np.min(low[:6]))
        state = 0
        for i in range(6, n):
            if close[i] > range_high:
                state = 1
            elif close[i] < range_low:
                state = -1
            signal[i] = state
    elif strategy == "VWAP_TREND":
        ema9 = pd.Series(close).ewm(span=9, adjust=False).mean().to_numpy()
        ema21 = pd.Series(close).ewm(span=21, adjust=False).mean().to_numpy()
        signal[(close > vwap) & (ema9 > ema21)] = 1
        signal[(close < vwap) & (ema9 < ema21)] = -1
    elif strategy == "VWAP_REVERSION":
        deviation = close - vwap
        scale = pd.Series(deviation).rolling(20, min_periods=10).std(ddof=0).to_numpy()
        z = np.divide(deviation, scale, out=np.full(n, np.nan), where=np.isfinite(scale) & (scale > 0))
        state = 0
        for i in range(n):
            if np.isfinite(z[i]) and z[i] <= -2.0:
                state = 1
            elif np.isfinite(z[i]) and z[i] >= 2.0:
                state = -1
            elif state == 1 and np.isfinite(z[i]) and z[i] >= 0:
                state = 0
            elif state == -1 and np.isfinite(z[i]) and z[i] <= 0:
                state = 0
            signal[i] = state
    elif strategy == "MOM60":
        for i in range(12, n):
            r = close[i] / close[i - 12] - 1.0
            signal[i] = 1 if r > 0 else (-1 if r < 0 else 0)
    else:
        raise ValueError("unknown strategy: " + strategy)

    # A signal on a bar close fills at the next open. Disallow fresh entries in the final 15 minutes.
    for i in range(max(0, n - 4), n):
        prev = signal[i - 1] if i > 0 else 0
        if prev == 0 and signal[i] != 0:
            signal[i] = 0
        elif prev != 0 and signal[i] == -prev:
            signal[i] = prev
    return signal


def session_funding_by_bar(g: pd.DataFrame, funding: pd.DataFrame) -> np.ndarray:
    n = len(g)
    out = np.zeros(n, dtype=float)
    if funding.empty:
        return out
    times = g.ts_ms.to_numpy(dtype=np.int64)
    end_times = np.append(times[1:], times[-1] + BAR_MS)
    relevant = funding[(funding.funding_ts_ms >= int(times[0])) & (funding.funding_ts_ms <= int(end_times[-1]))]
    for row in relevant.itertuples(index=False):
        idx = int(np.searchsorted(times, int(row.funding_ts_ms), side="right") - 1)
        if 0 <= idx < n and int(row.funding_ts_ms) <= int(end_times[idx]):
            out[idx] += float(row.funding_rate)
    return out


def metric_pack(returns, trades: list[dict] | None = None) -> dict:
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    n = int(len(r))
    if n == 0:
        out = {"sessions": 0, "net_return_pct": None, "cagr_pct": None, "sharpe": None,
               "sortino": None, "max_drawdown_pct": None, "daily_win_rate_pct": None}
        return out
    equity = np.cumprod(1.0 + r)
    total = float(equity[-1] - 1.0)
    cagr = float(equity[-1] ** (252.0 / n) - 1.0) if equity[-1] > 0 else None
    sd = float(np.std(r, ddof=1)) if n > 1 else 0.0
    sharpe = float(np.mean(r) / sd * math.sqrt(252.0)) if sd > 0 else None
    downside_dev = float(np.sqrt(np.mean(np.square(np.minimum(r, 0.0)))))
    sortino = float(np.mean(r) / downside_dev * math.sqrt(252.0)) if downside_dev > 0 else None
    peak = np.maximum.accumulate(np.concatenate(([1.0], equity)))[1:]
    drawdown = equity / peak - 1.0
    out = {
        "sessions": n, "net_return_pct": total * 100.0,
        "cagr_pct": None if cagr is None else cagr * 100.0, "sharpe": sharpe, "sortino": sortino,
        "max_drawdown_pct": float(np.min(drawdown) * 100.0),
        "daily_win_rate_pct": float(np.mean(r > 0) * 100.0),
        "mean_daily_return_bps": float(np.mean(r) * 10000.0),
        "daily_volatility_bps": sd * 10000.0,
    }
    if trades is not None:
        tr = [float(x["net_return"]) for x in trades if x.get("net_return") is not None and np.isfinite(x["net_return"])]
        winners = [x for x in tr if x > 0]
        losers = [x for x in tr if x < 0]
        gp, gl = sum(winners), -sum(losers)
        out.update({
            "trades": len(tr),
            "trade_win_rate_pct": (len(winners) / len(tr) * 100.0) if tr else None,
            "profit_factor": (gp / gl) if gl > 0 else (None if not tr else "NO_LOSERS"),
            "expectancy_bps_per_trade": (float(np.mean(tr)) * 10000.0) if tr else None,
            "median_trade_bps": (float(np.median(tr)) * 10000.0) if tr else None,
            "average_holding_minutes": float(np.mean([x["holding_minutes"] for x in trades])) if trades else None,
        })
    return out


def block_bootstrap_sharpe_ci(returns, seed: int, reps: int = N_BOOTSTRAPS) -> dict:
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 20 or np.std(r, ddof=1) == 0:
        return {"bootstrap_reps": reps, "block_sessions": BOOTSTRAP_BLOCK, "sharpe_ci_95": None}
    rng = np.random.default_rng(seed)
    n = len(r)
    blocks = int(math.ceil(n / BOOTSTRAP_BLOCK))
    vals = []
    starts = np.arange(n)
    within_block = np.arange(BOOTSTRAP_BLOCK)
    for _ in range(reps):
        chosen = rng.choice(starts, size=blocks, replace=True)
        idx = np.concatenate([(s + within_block) % n for s in chosen])[:n]
        sample = r[idx]
        sd = np.std(sample, ddof=1)
        if sd > 0:
            vals.append(float(np.mean(sample) / sd * math.sqrt(252.0)))
    ci = [float(np.quantile(vals, 0.025)), float(np.quantile(vals, 0.975))] if vals else None
    return {"bootstrap_reps": reps, "block_sessions": BOOTSTRAP_BLOCK, "sharpe_ci_95": ci}


def run_session(g: pd.DataFrame, signals: np.ndarray, funding: pd.DataFrame,
                slippage_bps: float, strategy: str, split: str) -> tuple[dict, list[dict]]:
    n = len(g)
    times = g.ts_ms.to_numpy(dtype=np.int64)
    opens = g.open.to_numpy(dtype=float)
    closes = g.close.to_numpy(dtype=float)
    exits = np.append(opens[1:], closes[-1])
    if strategy == "LONG_ONLY_BENCHMARK":
        pos = np.ones(n, dtype=int)  # Benchmark enters at the 09:30 open.
    else:
        pos = np.concatenate(([0], signals[:-1])).astype(int)
    per_bar_funding_rates = session_funding_by_bar(g, funding)
    fee_per_side = (TAKER_FEE_BPS + slippage_bps) / 10000.0

    net_bars = []
    daily_gross = daily_cost = daily_funding = turnover = 0.0
    previous = 0
    for i in range(n):
        gross = pos[i] * (exits[i] / opens[i] - 1.0)
        traded = abs(int(pos[i]) - int(previous))
        if i == n - 1:
            traded += abs(int(pos[i]))
        cost = traded * fee_per_side
        fund = -float(pos[i]) * float(per_bar_funding_rates[i])
        net_bars.append(gross - cost + fund)
        daily_gross += gross
        daily_cost += cost
        daily_funding += fund
        turnover += traded
        previous = int(pos[i])

    trades = []
    current = 0
    entry_i = None
    entry_px = None
    funding_accum = 0.0
    for i in range(n):
        px = opens[i]
        desired = int(pos[i])
        if desired != current:
            if current != 0 and entry_i is not None:
                gross_trade = current * (px / entry_px - 1.0)
                net_trade = gross_trade + funding_accum - 2.0 * fee_per_side
                trades.append({
                    "experiment_id": EXPERIMENT_ID, "strategy": strategy, "split": split,
                    "direction": "LONG" if current > 0 else "SHORT",
                    "entry_utc": pd.to_datetime(int(times[entry_i]), unit="ms", utc=True).isoformat(),
                    "exit_utc": pd.to_datetime(int(times[i]), unit="ms", utc=True).isoformat(),
                    "entry_price": float(entry_px), "exit_price": float(px),
                    "gross_return": float(gross_trade), "funding_return": float(funding_accum),
                    "net_return": float(net_trade), "holding_minutes": float((times[i] - times[entry_i]) / 60000.0),
                })
            current = desired
            entry_i = i if current != 0 else None
            entry_px = px if current != 0 else None
            funding_accum = 0.0
        if current != 0:
            funding_accum += -float(current) * float(per_bar_funding_rates[i])

    if current != 0 and entry_i is not None:
        exit_px = float(closes[-1])
        gross_trade = current * (exit_px / entry_px - 1.0)
        net_trade = gross_trade + funding_accum - 2.0 * fee_per_side
        trades.append({
            "experiment_id": EXPERIMENT_ID, "strategy": strategy, "split": split,
            "direction": "LONG" if current > 0 else "SHORT",
            "entry_utc": pd.to_datetime(int(times[entry_i]), unit="ms", utc=True).isoformat(),
            "exit_utc": pd.to_datetime(int(times[-1] + BAR_MS), unit="ms", utc=True).isoformat(),
            "entry_price": float(entry_px), "exit_price": exit_px,
            "gross_return": float(gross_trade), "funding_return": float(funding_accum),
            "net_return": float(net_trade), "holding_minutes": float((times[-1] + BAR_MS - times[entry_i]) / 60000.0),
        })
    return {
        "net_return": float(np.prod(1.0 + np.asarray(net_bars, dtype=float)) - 1.0),
        "gross_return_sum": float(daily_gross),
        "transaction_cost_return_sum": float(daily_cost),
        "funding_return_sum": float(daily_funding),
        "turnover_units": float(turnover),
        "exposure_fraction": float(np.mean(pos != 0)),
        "bar_count": n,
    }, trades


def main(output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    spec_path = Path("research/pepeusdt_session/spec_v2.json")
    spec_bytes = spec_path.read_bytes() if spec_path.exists() else b"SPEC_NOT_FOUND"
    base_manifest = {
        "experiment_id": EXPERIMENT_ID, "status": "RUNNING",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": os.environ.get("GITHUB_SHA", "LOCAL_OR_UNKNOWN"),
        "spec_sha256": sha256_bytes(spec_bytes),
        "runner": os.environ.get("RUNNER_NAME", "unknown"),
        "instrument_id": INSTRUMENT_ID, "bar": "5m",
        "session": "NYSE 09:30-16:00 America/New_York, full 78-bar sessions only",
        "fee_bps_per_fill": TAKER_FEE_BPS, "slippage_bps_per_fill": SLIPPAGE_BPS,
        "candidates": ["ORB30", "VWAP_TREND", "VWAP_REVERSION", "MOM60"],
    }
    try:
        bars, data_info = fetch_candles()
        candles_columns = ["ts_ms", "open", "high", "low", "close", "vol", "volCcy", "volCcyQuote", "confirm"]
        candles_bytes = canonical_csv_bytes(bars, candles_columns)
        bars_sha = sha256_bytes(candles_bytes)
        save_gzip_deterministic(output_dir / "pepe_usdt_swap_5m.csv.gz", candles_bytes)

        funding, funding_info = fetch_funding()
        funding_cols = ["funding_ts_ms", "funding_rate", "funding_rate_predicted_field", "realized_rate_field"]
        funding_bytes = canonical_csv_bytes(funding, funding_cols) if not funding.empty else b"funding_ts_ms,funding_rate,funding_rate_predicted_field,realized_rate_field\n"
        save_gzip_deterministic(output_dir / "pepe_usdt_swap_funding.csv.gz", funding_bytes)
        funding_sha = sha256_bytes(funding_bytes)

        sessions, session_info = market_sessions(bars)
        if len(sessions) < 100:
            raise RuntimeError(f"Insufficient complete NYSE sessions for a deep backtest: {len(sessions)}.")
        session_dates = [x[0] for x in sessions]
        n_total = len(session_dates)
        n_discovery = int(n_total * 0.60)
        n_validation = int(n_total * 0.20)
        split_by_date = {}
        for i, d in enumerate(session_dates):
            split_by_date[d] = "DISCOVERY" if i < n_discovery else ("VALIDATION" if i < n_discovery + n_validation else "FINAL_OOS")

        strategies = ["ORB30", "VWAP_TREND", "VWAP_REVERSION", "MOM60"]
        rows_daily, rows_trades, rows_metrics, yearly = [], [], [], []
        all_strategy_returns = {}
        benchmark_records = []

        for date, g in sessions:
            split = split_by_date[date]
            for scenario, slip in SLIPPAGE_BPS.items():
                info, _ = run_session(g, np.ones(len(g), dtype=int), funding, slip, "LONG_ONLY_BENCHMARK", split)
                benchmark_records.append({
                    "session_date": date, "split": split, "cost_scenario": scenario,
                    "net_return": info["net_return"], "gross_return_sum": info["gross_return_sum"],
                    "transaction_cost_return_sum": info["transaction_cost_return_sum"],
                    "funding_return_sum": info["funding_return_sum"], "turnover_units": info["turnover_units"],
                    "exposure_fraction": info["exposure_fraction"], "strategy": "LONG_ONLY_BENCHMARK"
                })

        for strategy in strategies:
            for scenario, slip in SLIPPAGE_BPS.items():
                candidate_daily = []
                for date, g in sessions:
                    split = split_by_date[date]
                    sig = compute_signals(g, strategy)
                    info, trades = run_session(g, sig, funding, slip, strategy, split)
                    for t in trades:
                        t["cost_scenario"] = scenario
                        rows_trades.append(t)
                    rec = {
                        "experiment_id": EXPERIMENT_ID, "session_date": date,
                        "strategy": strategy, "split": split, "cost_scenario": scenario,
                        "net_return": info["net_return"], "gross_return_sum": info["gross_return_sum"],
                        "transaction_cost_return_sum": info["transaction_cost_return_sum"],
                        "funding_return_sum": info["funding_return_sum"], "turnover_units": info["turnover_units"],
                        "exposure_fraction": info["exposure_fraction"], "bar_count": info["bar_count"],
                    }
                    candidate_daily.append(rec)
                    rows_daily.append(rec)
                all_strategy_returns[(strategy, scenario)] = candidate_daily

        for scenario in SLIPPAGE_BPS:
            for split in ["DISCOVERY", "VALIDATION", "FINAL_OOS"]:
                part = [x for x in benchmark_records if x["cost_scenario"] == scenario and x["split"] == split]
                m = metric_pack([x["net_return"] for x in part])
                m.update({"strategy": "LONG_ONLY_BENCHMARK", "split": split, "cost_scenario": scenario})
                rows_metrics.append(m)

        for strategy in strategies:
            for scenario in SLIPPAGE_BPS:
                daily = all_strategy_returns[(strategy, scenario)]
                for split in ["DISCOVERY", "VALIDATION", "FINAL_OOS"]:
                    part = [x for x in daily if x["split"] == split]
                    trade_part = [t for t in rows_trades if t["strategy"] == strategy and t["cost_scenario"] == scenario and t["split"] == split]
                    m = metric_pack([x["net_return"] for x in part], trade_part)
                    m.update({"strategy": strategy, "split": split, "cost_scenario": scenario})
                    rows_metrics.append(m)

        funding_start = None if funding.empty else pd.to_datetime(int(funding.funding_ts_ms.min()), unit="ms", utc=True)
        if funding_start is not None:
            covered_dates = {d for d in session_dates if pd.Timestamp(d, tz=NY).tz_convert("UTC") >= funding_start.tz_convert("UTC")}
            funding_coverage_sessions = len(covered_dates) / max(1, n_total)
        else:
            funding_coverage_sessions = 0.0

        promotion, oos_halves = {}, {}
        for strategy in strategies:
            base_daily = all_strategy_returns[(strategy, "BASE")]
            oos_rows = [x for x in base_daily if x["split"] == "FINAL_OOS"]
            oos_returns = [x["net_return"] for x in oos_rows]
            oos_trades = [t for t in rows_trades if t["strategy"] == strategy and t["cost_scenario"] == "BASE" and t["split"] == "FINAL_OOS"]
            oos_metric = metric_pack(oos_returns, oos_trades)
            oos_metric.update(block_bootstrap_sharpe_ci(oos_returns, seed=20261010 + strategies.index(strategy)))
            severe_rows = [x for x in all_strategy_returns[(strategy, "STRESS_4X_SLIPPAGE")] if x["split"] == "FINAL_OOS"]
            severe_metric = metric_pack([x["net_return"] for x in severe_rows])
            halves = [oos_rows[:len(oos_rows)//2], oos_rows[len(oos_rows)//2:]]
            half_metrics = [metric_pack([x["net_return"] for x in h]) for h in halves]
            both_halves_positive = bool((half_metrics[0].get("net_return_pct") or 0) > 0 and (half_metrics[1].get("net_return_pct") or 0) > 0)
            oos_halves[strategy] = {"first_half": half_metrics[0], "second_half": half_metrics[1], "both_halves_net_positive": both_halves_positive}
            yearly_values = sorted({x["session_date"][:4] for x in base_daily})
            for year in yearly_values:
                yrows = [x for x in base_daily if x["session_date"].startswith(year)]
                yearly.append({"strategy": strategy, "year": year, **metric_pack([x["net_return"] for x in yrows])})
            benchmark_oos = [x for x in benchmark_records if x["split"] == "FINAL_OOS" and x["cost_scenario"] == "BASE"]
            benchmark_returns = [x["net_return"] for x in benchmark_oos]
            benchmark_net = float(np.prod(1.0 + np.asarray(benchmark_returns)) - 1.0) if benchmark_returns else 0.0
            candidate_net = float(np.prod(1.0 + np.asarray(oos_returns)) - 1.0) if oos_returns else 0.0
            checks = {
                "min_oos_trades": len(oos_trades) >= MIN_FINAL_OOS_TRADES,
                "min_oos_sharpe": isinstance(oos_metric.get("sharpe"), (int, float)) and oos_metric["sharpe"] >= MIN_OOS_SHARPE,
                "positive_oos_net_return": candidate_net > 0,
                "beat_long_only_session_benchmark": candidate_net > benchmark_net,
                "both_oos_halves_positive": both_halves_positive,
                "positive_severe_cost_sharpe": isinstance(severe_metric.get("sharpe"), (int, float)) and severe_metric["sharpe"] > 0,
                "funding_coverage_minimum": funding_coverage_sessions >= MIN_FUNDING_COVERAGE,
            }
            promotion[strategy] = {
                "eligible": bool(all(checks.values())),
                "status": "PASS_FIXED_GATE" if all(checks.values()) else ("NON_PROMOTABLE_INCOMPLETE_FUNDING_HISTORY" if not checks["funding_coverage_minimum"] else "FAIL_FIXED_GATE"),
                "checks": checks, "base_oos_metrics": oos_metric, "severe_slippage_oos_metrics": severe_metric,
                "oos_halves": oos_halves[strategy],
                "oos_compounded_net_return_pct": candidate_net * 100.0,
                "long_only_oos_compounded_net_return_pct": benchmark_net * 100.0,
            }

        daily_df, trades_df = pd.DataFrame(rows_daily), pd.DataFrame(rows_trades)
        metrics_df, yearly_df = pd.DataFrame(rows_metrics), pd.DataFrame(yearly)
        benchmark_df = pd.DataFrame(benchmark_records)
        daily_df.to_csv(output_dir / "daily_returns.csv", index=False, float_format="%.12g")
        trades_df.to_csv(output_dir / "trades.csv", index=False, float_format="%.12g")
        metrics_df.to_csv(output_dir / "metrics.csv", index=False, float_format="%.12g")
        yearly_df.to_csv(output_dir / "calendar_year_metrics.csv", index=False, float_format="%.12g")
        benchmark_df.to_csv(output_dir / "benchmark_daily_returns.csv", index=False, float_format="%.12g")

        summary = {
            "experiment_id": EXPERIMENT_ID,
            "status": "EXECUTED_EXPLORATORY_NO_PROMOTION" if not any(v["eligible"] for v in promotion.values()) else "EXECUTED_FIXED_GATE_PASSED",
            "instrument_id": INSTRUMENT_ID, "tradingview_symbol": "PEPEUSDT.P (OKX)",
            "data": {
                **data_info, "canonical_ohlcv_sha256": bars_sha, "funding_canonical_sha256": funding_sha,
                "data_start_utc": pd.to_datetime(int(bars.ts_ms.min()), unit="ms", utc=True).isoformat(),
                "data_end_utc": pd.to_datetime(int(bars.ts_ms.max()), unit="ms", utc=True).isoformat(),
                "bar_interval": "5m", "price_history_candle_count": int(len(bars)),
                "funding": funding_info, "funding_coverage_fraction_of_sessions": funding_coverage_sessions,
                "funding_coverage_pct": funding_coverage_sessions * 100.0,
            },
            "session_filters": session_info,
            "chronological_split": {
                "discovery_sessions": n_discovery, "validation_sessions": n_validation,
                "final_oos_sessions": n_total - n_discovery - n_validation,
                "discovery_end": session_dates[n_discovery - 1] if n_discovery else None,
                "validation_end": session_dates[n_discovery + n_validation - 1] if n_discovery + n_validation else None,
                "final_oos_start": session_dates[n_discovery + n_validation] if n_total > n_discovery + n_validation else None,
                "final_oos_end": session_dates[-1],
                "split_rule": "Chronological 60% / 20% / 20% of complete sessions; candidate parameters were not searched."
            },
            "cost_model": {
                "taker_fee_bps_per_fill": TAKER_FEE_BPS, "slippage_bps_per_fill": SLIPPAGE_BPS,
                "all_in_bps_per_fill": {key: TAKER_FEE_BPS + val for key, val in SLIPPAGE_BPS.items()},
                "funding_application": "Actual realized OKX rates applied to 5-minute position intervals when present. Older sessions are not funding-complete."
            },
            "candidate_promotion_gate": {
                "min_final_oos_trades": MIN_FINAL_OOS_TRADES, "min_baseline_net_oos_sharpe": MIN_OOS_SHARPE,
                "positive_oos_net_return": True, "beat_long_only_session_benchmark": True,
                "both_oos_halves_positive": True, "positive_severe_cost_oos_sharpe": True,
                "funding_coverage_minimum": MIN_FUNDING_COVERAGE,
            },
            "promotion_decisions": promotion, "oos_halves": oos_halves, "calendar_year_metrics": yearly,
            "metrics_rows": json.loads(metrics_df.to_json(orient="records")),
            "limitations": [
                "5-minute OHLCV is not tick/order-book execution data; fills and slippage are proxy assumptions.",
                "OKX public funding-rate history exposes up to three months; older sessions cannot be fully funding-adjusted, so no candidate is promotable from full-history results.",
                "Backtest uses 1x notional, session-only positions, no live orders, and no liquidation modeling.",
                "Final OOS is the last 20% of complete sessions and is not used to choose parameters."
            ],
        }
        write_json(output_dir / "summary.json", summary)
        lines = [
            "# HARMONY PEPEUSDT.P NYSE-session backtest v2", "",
            f"- Experiment: {EXPERIMENT_ID}",
            f"- Instrument: {INSTRUMENT_ID} (TradingView PEPEUSDT.P, OKX).",
            f"- Price data: {summary['data']['first_candle_utc']} to {summary['data']['last_confirmed_candle_utc']}; {summary['data']['row_count']:,} confirmed 5-minute candles.",
            f"- Canonical OHLCV SHA-256: {bars_sha}",
            f"- Sessions: {n_total:,} complete NYSE regular sessions, 78 five-minute bars each.",
            f"- Chronological splits: discovery {n_discovery:,} through {summary['chronological_split']['discovery_end']}; validation {n_validation:,} through {summary['chronological_split']['validation_end']}; final OOS {n_total-n_discovery-n_validation:,} from {summary['chronological_split']['final_oos_start']} through {summary['chronological_split']['final_oos_end']}.",
            f"- Funding history coverage: {funding_coverage_sessions*100:.1f}% of sessions by date. {funding_info.get('coverage_limit_note','')}",
            "", "## Frozen methods", "",
            "ORB30: 30-minute opening-range breakout. VWAP_TREND: session VWAP plus EMA(9/21). VWAP_REVERSION: rolling 20-bar VWAP-deviation z-score, fixed +/-2 entry and zero-cross exit. MOM60: direction of the prior 60-minute return.",
            "Signals use completed-bar closes and execute at the next 5-minute open. Positions are forced flat at the 16:00 New York session close. Only NYSE dates whose scheduled open is 09:30 and scheduled close is 16:00 New York time are retained; holidays, early-close sessions, and incomplete sessions are excluded.",
            "", "## Final out-of-sample results at base costs", "",
            "| Candidate | Net OOS return | OOS Sharpe | OOS max DD | OOS trades | Severe-slippage Sharpe | Promotion decision |",
            "|---|---:|---:|---:|---:|---:|---|"
        ]
        for strategy in strategies:
            p = promotion[strategy]
            m = p["base_oos_metrics"]
            sm = p["severe_slippage_oos_metrics"]
            lines.append(
                f"| {strategy} | {m.get('net_return_pct') if m.get('net_return_pct') is not None else 'NA'}% | "
                f"{m.get('sharpe') if m.get('sharpe') is not None else 'NA'} | "
                f"{m.get('max_drawdown_pct') if m.get('max_drawdown_pct') is not None else 'NA'}% | "
                f"{m.get('trades', 0)} | {sm.get('sharpe') if sm.get('sharpe') is not None else 'NA'} | {p['status']} |"
            )
        lines.extend([
            "", "## Interpretation guardrails", "",
            "No strategy is promoted unless every fixed gate passes. Funding coverage below 95% automatically blocks promotion regardless of apparent price-only or partially funded performance.",
            "Base costs are a 5 bps taker fee plus 5 bps slippage per fill; stress cases use 10 and 20 bps slippage per fill. Funding uses official OKX realized rates only during the endpoint's available history.",
            "A good OHLCV result may disappear under tick-level fills, spread, market impact, fee-tier differences, complete funding history, or later out-of-sample data."
        ])
        (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

        manifest = {
            **base_manifest, "status": summary["status"], "data": summary["data"],
            "session_filters": session_info, "split": summary["chronological_split"],
            "output_sha256": {
                "summary.json": sha256_bytes((output_dir / "summary.json").read_bytes()),
                "report.md": sha256_bytes((output_dir / "report.md").read_bytes()),
                "daily_returns.csv": sha256_bytes((output_dir / "daily_returns.csv").read_bytes()),
                "trades.csv": sha256_bytes((output_dir / "trades.csv").read_bytes()),
                "metrics.csv": sha256_bytes((output_dir / "metrics.csv").read_bytes()),
            },
            "promotion_decisions": {k: v["status"] for k, v in promotion.items()},
        }
        write_json(output_dir / "manifest.json", manifest)
        print(json.dumps({
            "status": summary["status"], "data": data_info, "sessions": n_total,
            "promotion_decisions": {k: v["status"] for k, v in promotion.items()}
        }, indent=2))
        return 0

    except Exception as exc:
        base_manifest.update({
            "status": "FAILED", "failure": str(exc),
            "traceback": traceback.format_exc(), "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        })
        write_json(output_dir / "manifest.json", base_manifest)
        (output_dir / "report.md").write_text(
            "# HARMONY PEPEUSDT.P backtest did not complete\n\n"
            f"- Experiment: {EXPERIMENT_ID}\n- Status: FAILED\n- Error: {str(exc)}\n\n"
            "No strategy-performance claim is supported by this failed execution.\n", encoding="utf-8"
        )
        for filename, contents in [
            ("summary.json", "{}\n"), ("daily_returns.csv", ""), ("trades.csv", ""),
            ("metrics.csv", ""), ("calendar_year_metrics.csv", ""), ("benchmark_daily_returns.csv", "")
        ]:
            p = output_dir / filename
            if not p.exists():
                p.write_text(contents, encoding="utf-8")
        print(traceback.format_exc(), file=sys.stderr)
        return 2


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("build/pepeusdt")
    sys.exit(main(target))
