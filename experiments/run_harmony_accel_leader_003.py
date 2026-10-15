#!/usr/bin/env python3
"""Preregistered exploratory backtest: fresh crypto acceleration leader + ATR trail."""
from __future__ import annotations

import gzip, hashlib, io, json, math, os, sys, traceback
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

EXPERIMENT_ID = "HARMONY-ACCEL-LEADER-003"
START = pd.Timestamp("2023-01-01T00:00:00Z")
BAR_DELTA = pd.Timedelta(minutes=15)
BAR_MS = 900_000
SYMBOLS = [
    "BTC-USDT",
    "ETH-USDT",
    "LTC-USDT",
    "XRP-USDT",
    "BNB-USDT",
    "BCH-USDT",
    "ADA-USDT",
    "DOGE-USDT",
    "SOL-USDT",
    "LINK-USDT",
    "DOT-USDT",
    "AVAX-USDT",
    "TRX-USDT",
    "ETC-USDT",
    "XLM-USDT",
    "ATOM-USDT",
    "FIL-USDT",
    "OP-USDT",
    "UNI-USDT",
    "AAVE-USDT",
    "ALGO-USDT",
    "NEAR-USDT",
    "MANA-USDT",
    "SAND-USDT",
]
FEE_BPS, SLIPPAGE_BPS = 5.0, [5.0, 10.0, 20.0]
ATR_PERIOD, ATR_MULTIPLIER = 14, 3.0
FRESH_BARS, Z_THRESHOLD, VOL_HOURS = 96, 1.0, 168
MIN_HISTORY_DAYS = 730
MAX_GAP_FRACTION, MAX_CONSECUTIVE_GAP_BARS, MAX_TAIL_AGE_HOURS = 0.001, 4, 2.0
BOOTSTRAPS, BOOTSTRAP_BLOCK_DAYS, SEED = 1000, 5, 1401
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / EXPERIMENT_ID
SPEC_PATH = ROOT / "experiments" / "HARMONY-ACCEL-LEADER-003.yaml"


class DataGateBlocked(RuntimeError):
    """An intentional fail-closed result, not a strategy failure."""


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def finite(x) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def freshness_ok(last_seen_index: int | None, current_index: int, window_bars: int = FRESH_BARS) -> bool:
    return last_seen_index is None or (current_index - last_seen_index) > window_bars


def stop_trigger_price(open_price: float, low_price: float, stop: float) -> float | None:
    """A gap through the stop fills at the open; otherwise a touched stop fills at its level."""
    if not all(finite(x) for x in (open_price, low_price, stop)):
        return None
    if open_price <= stop:
        return float(open_price)
    if low_price <= stop:
        return float(stop)
    return None


def ratchet_stop(previous_stop: float, high_water: float, atr: float, multiplier: float = ATR_MULTIPLIER) -> float:
    candidate = high_water - multiplier * atr if finite(atr) and atr >= 0 else previous_stop
    return float(max(previous_stop, candidate))


def compute_features(df: pd.DataFrame, index: pd.DatetimeIndex) -> pd.DataFrame:
    raw = df.copy()
    raw.index = pd.to_datetime(raw.ts_ms, unit="ms", utc=True)
    raw = raw[["open", "high", "low", "close", "volume", "quote_volume"]].reindex(index)
    close = raw.close.astype(float)
    ret15 = np.log(close / close.shift(1)).where(close.notna() & close.shift(1).notna())
    m1h = ret15.rolling(4, min_periods=4).sum()
    accel = m1h - m1h.shift(4)
    hour_boundaries = index[index.minute == 45]
    completed_hour_returns = m1h.reindex(hour_boundaries)
    sigma_hour = completed_hour_returns.rolling(VOL_HOURS, min_periods=VOL_HOURS).std(ddof=0)
    sigma_15m = sigma_hour.reindex(index).ffill()
    z_accel = accel / sigma_15m.replace(0.0, np.nan)
    return24 = np.expm1(ret15.rolling(96, min_periods=96).sum())
    prev_close = close.shift(1)
    tr = pd.concat([raw.high - raw.low, (raw.high - prev_close).abs(),
                    (raw.low - prev_close).abs()], axis=1).max(axis=1)
    tr = tr.where(prev_close.notna() & raw.high.notna() & raw.low.notna())
    atr = tr.rolling(ATR_PERIOD, min_periods=ATR_PERIOD).mean()
    return pd.DataFrame({"open": raw.open, "high": raw.high, "low": raw.low, "close": close,
                         "m1h": m1h, "acceleration": accel, "z_acceleration": z_accel,
                         "return_24h": return24, "atr": atr}, index=index)


def leader_signals(features: dict[str, pd.DataFrame], index: pd.DatetimeIndex) -> dict[str, dict[int, dict]]:
    specs = {
        "primary_normalized_fresh": ("z_acceleration", True, "normalized"),
        "baseline_fresh_24h_gainer": ("return_24h", True, "gainer"),
        "ablation_raw_acceleration_fresh": ("acceleration", True, "raw"),
        "ablation_normalized_no_freshness": ("z_acceleration", False, "normalized"),
    }
    signals = {k: {} for k in specs}
    last_leader = {k: {} for k in specs}
    arrays = {s: {c: features[s][c].to_numpy(dtype=float)
                  for c in ("m1h", "acceleration", "z_acceleration", "return_24h", "close", "atr")}
              for s in features}
    for i in range(len(index)):
        for name, (score_column, require_fresh, mode) in specs.items():
            ranked = [(float(arrays[s][score_column][i]), s) for s in sorted(features)
                      if finite(arrays[s][score_column][i])]
            if not ranked:
                continue
            ranked.sort(key=lambda x: (-x[0], x[1]))
            score, symbol = ranked[0]
            was_fresh = freshness_ok(last_leader[name].get(symbol), i)
            last_leader[name][symbol] = i
            a = arrays[symbol]
            m1h, accel, z, r24, close, atr = (a[k][i] for k in
                                               ("m1h", "acceleration", "z_acceleration", "return_24h", "close", "atr"))
            if not all(finite(v) for v in (m1h, close, atr)) or atr <= 0 or m1h <= 0:
                continue
            qualifies = ((mode == "normalized" and finite(z) and z >= Z_THRESHOLD)
                         or (mode == "raw" and finite(accel) and accel > 0)
                         or (mode == "gainer" and finite(r24) and r24 > 0))
            if require_fresh and not was_fresh:
                qualifies = False
            if qualifies:
                signals[name][i] = {
                    "symbol": symbol, "signal_index": i, "signal_time_utc": index[i].isoformat(),
                    "signal_close": float(close), "atr_at_signal": float(atr), "score": float(score),
                    "z_acceleration": float(z) if finite(z) else None,
                    "m1h_log_return": float(m1h), "return_24h": float(r24) if finite(r24) else None,
                }
    return signals


def simulate_segment(features: dict[str, pd.DataFrame], index: pd.DatetimeIndex, signals: dict[int, dict],
                     start_i: int, end_i: int, slippage_bps: float) -> dict:
    if end_i <= start_i:
        return {"equity": np.array([]), "trades": [], "cancelled_gap_entries": 0, "time_in_market": 0.0}
    slip, fee = slippage_bps / 10000, FEE_BPS / 10000
    arr = {s: {c: features[s][c].to_numpy(dtype=float) for c in ("open", "high", "low", "close", "atr")}
           for s in features}
    nav, pos, pending = 1.0, None, None
    equity, trades, cancellations, exposure = [], [], 0, 0

    def close_trade(position, i, exit_mid, reason):
        nonlocal nav
        exit_fill = float(exit_mid) * (1 - slip)
        nav_after = position["units"] * exit_fill * (1 - fee)
        gross = float(exit_mid) / position["entry_mid"] - 1
        held = max(1, i - position["entry_i"] + 1)
        trades.append({
            "signal_time_utc": position["signal_time_utc"],
            "entry_time_utc": index[position["entry_i"]].isoformat(),
            "exit_time_utc": (index[i] + BAR_DELTA).isoformat(),
            "symbol": position["symbol"],
            "entry_reference_price": position["entry_mid"], "entry_fill_price": position["entry_fill"],
            "exit_reference_price": float(exit_mid), "exit_fill_price": exit_fill,
            "initial_stop": position["initial_stop"], "final_stop": position["stop"],
            "exit_reason": reason, "signal_score": position["signal_score"],
            "signal_z_acceleration": position.get("signal_z_acceleration"),
            "signal_m1h_log_return": position["signal_m1h_log_return"],
            "held_bars": held, "holding_hours": held * 0.25, "gross_price_return": gross,
            "net_trade_return_on_entry_nav": nav_after / position["nav_before_entry"] - 1,
            "nav_before_entry": position["nav_before_entry"], "nav_after_exit": nav_after,
            "slippage_bps_each_side": slippage_bps, "fee_bps_each_side": FEE_BPS,
        })
        nav = float(nav_after)

    for i in range(start_i, end_i):
        if pending is not None:
            sym, aa = pending["symbol"], arr[pending["symbol"]]
            op = aa["open"][i]
            initial_stop = pending["signal_close"] - ATR_MULTIPLIER * pending["atr_at_signal"]
            if not finite(op) or op <= initial_stop:
                cancellations += 1
            else:
                before, entry_mid = nav, float(op)
                fill = entry_mid * (1 + slip)
                units = before * (1 - fee) / fill
                nav = units * entry_mid
                pos = {"symbol": sym, "signal_time_utc": pending["signal_time_utc"],
                       "signal_score": pending["score"], "signal_z_acceleration": pending.get("z_acceleration"),
                       "signal_m1h_log_return": pending["m1h_log_return"], "entry_i": i,
                       "entry_mid": entry_mid, "entry_fill": fill, "nav_before_entry": before,
                       "units": units, "initial_stop": float(initial_stop), "stop": float(initial_stop),
                       "high_water": entry_mid}
            pending = None

        if pos is not None:
            aa = arr[pos["symbol"]]
            op, hi, lo, cl = (aa[c][i] for c in ("open", "high", "low", "close"))
            if not all(finite(x) for x in (op, hi, lo, cl)):
                raise DataGateBlocked(f"Missing OHLC while holding {pos['symbol']} at {index[i].isoformat()}")
            hit = stop_trigger_price(float(op), float(lo), float(pos["stop"]))
            if hit is not None:
                close_trade(pos, i, hit, "trailing_stop")
                pos = None
            else:
                nav = float(pos["units"] * cl)
                exposure += 1
                pos["high_water"] = max(float(pos["high_water"]), float(hi))
                atr_now = aa["atr"][i]
                if finite(atr_now) and atr_now > 0:
                    pos["stop"] = ratchet_stop(pos["stop"], pos["high_water"], atr_now)
        if pos is None and pending is None and i in signals and i + 1 < end_i:
            pending = dict(signals[i])
        equity.append(float(nav))

    if pos is not None:
        last_i = end_i - 1
        last_close = arr[pos["symbol"]]["close"][last_i]
        if not finite(last_close):
            raise DataGateBlocked("Cannot terminally liquidate: missing final close")
        close_trade(pos, last_i, float(last_close), "terminal_liquidation")
        equity[-1] = float(nav)
    return {"equity": np.asarray(equity), "trades": trades, "cancelled_gap_entries": cancellations,
            "time_in_market": exposure / max(1, end_i - start_i)}


def daily_equity(equity: np.ndarray, index: pd.DatetimeIndex, start_i: int) -> pd.Series:
    ix = index[start_i:start_i + len(equity)] + BAR_DELTA
    return pd.Series(equity, index=ix).resample("1D").last().dropna()


def benchmark_equal_weight(features: dict[str, pd.DataFrame], start_i: int, end_i: int,
                           slippage_bps: float) -> np.ndarray:
    slip, fee = slippage_bps / 10000, FEE_BPS / 10000
    paths = []
    for s in sorted(features):
        close = features[s].close.to_numpy(dtype=float)[start_i:end_i]
        if len(close) != end_i - start_i or not np.isfinite(close).all() or close[0] <= 0:
            raise DataGateBlocked(f"Invalid equal-weight benchmark prices for {s}")
        paths.append(close / close[0])
    equity = ((1 - fee) / (1 + slip)) * np.mean(np.vstack(paths), axis=0)
    equity[-1] *= (1 - slip) * (1 - fee)
    return equity.astype(float)


def performance_metrics(eq_bars: np.ndarray, bm_bars: np.ndarray, index: pd.DatetimeIndex,
                        start_i: int, end_i: int, trades: list[dict], exposure: float,
                        cancellations: int) -> tuple[dict, pd.DataFrame]:
    eq, bm = daily_equity(eq_bars, index, start_i), daily_equity(bm_bars, index, start_i)
    ix = eq.index.intersection(bm.index)
    eq, bm = eq.reindex(ix), bm.reindex(ix)
    if eq.empty:
        return {"days": 0}, pd.DataFrame()
    sr, br = eq.pct_change(), bm.pct_change()
    sr.iloc[0], br.iloc[0] = float(eq.iloc[0]) - 1, float(bm.iloc[0]) - 1
    sr, br = sr.fillna(0), br.fillna(0)
    r, b = sr.to_numpy(float), br.to_numpy(float)
    excess = r - b
    span_days = max(1.0, ((index[end_i - 1] + BAR_DELTA) - index[start_i]).total_seconds() / 86400)
    final_nav = float(eq_bars[-1])
    cagr = final_nav ** (365.25 / span_days) - 1 if final_nav > 0 else -1.0
    std = float(np.std(r, ddof=1)) if len(r) > 1 else 0.0
    downside = float(np.sqrt(np.mean(np.square(np.minimum(r, 0)))) if len(r) else 0.0)
    sharpe = float(np.mean(r) / std * math.sqrt(365.25)) if std > 0 else 0.0
    sortino = float(np.mean(r) / downside * math.sqrt(365.25)) if downside > 0 else 0.0
    path = np.r_[1.0, eq.to_numpy(float)]
    dd = path / np.maximum.accumulate(path) - 1
    tr = np.array([float(t["net_trade_return_on_entry_nav"]) for t in trades], float)
    wins, losses = tr[tr > 0], tr[tr < 0]
    profit_factor = float(wins.sum() / abs(losses.sum())) if len(losses) and abs(losses.sum()) else None
    hold = np.array([t["holding_hours"] for t in trades], float)
    concentration = float(wins.max() / wins.sum()) if len(wins) and wins.sum() > 0 else None
    m = {
        "days": int(len(eq)), "calendar_span_days": span_days, "final_nav": final_nav,
        "net_total_return": final_nav - 1, "net_cagr": float(cagr),
        "annualized_sharpe": float(sharpe), "annualized_sortino": float(sortino),
        "maximum_drawdown": float(dd.min()), "benchmark_final_nav": float(bm_bars[-1]),
        "benchmark_net_total_return": float(bm_bars[-1] - 1),
        "daily_mean_return": float(np.mean(r)), "mean_daily_excess_vs_equal_weight_hold": float(np.mean(excess)),
        "closed_trade_count": int(len(tr)), "win_rate": float(np.mean(tr > 0)) if len(tr) else None,
        "profit_factor": profit_factor, "expectancy_per_trade": float(np.mean(tr)) if len(tr) else None,
        "median_trade_net_return": float(np.median(tr)) if len(tr) else None,
        "mean_holding_hours": float(np.mean(hold)) if len(hold) else None,
        "median_holding_hours": float(np.median(hold)) if len(hold) else None,
        "time_in_market_fraction": float(exposure), "largest_winner_share_of_winning_returns": concentration,
        "cancelled_due_to_gap_entry": int(cancellations),
    }
    daily = pd.DataFrame({
        "date_utc": [d.date().isoformat() for d in ix],
        "strategy_nav": eq.to_numpy(float), "strategy_daily_return": r,
        "benchmark_nav": bm.to_numpy(float), "benchmark_daily_return": b,
        "daily_excess_return_vs_equal_weight_hold": excess,
    })
    return m, daily


def moving_block_bootstrap(primary: np.ndarray, comparator: np.ndarray) -> dict:
    n = min(len(primary), len(comparator))
    if n < max(30, BOOTSTRAP_BLOCK_DAYS * 4):
        return {"status": "inconclusive_insufficient_daily_observations", "observations": n}
    p, c = np.asarray(primary[:n], float), np.asarray(comparator[:n], float)
    rng = np.random.default_rng(SEED)
    blocks = int(math.ceil(n / BOOTSTRAP_BLOCK_DAYS))
    max_start = n - BOOTSTRAP_BLOCK_DAYS + 1
    bp, bd = [], []
    for _ in range(BOOTSTRAPS):
        starts = rng.integers(0, max_start, size=blocks)
        ix = np.concatenate([np.arange(x, x + BOOTSTRAP_BLOCK_DAYS) for x in starts])[:n]
        bp.append(float(np.mean(p[ix])))
        bd.append(float(np.mean((p - c)[ix])))
    return {
        "status": "computed", "observations": n, "method": "moving_block_bootstrap_paired_daily_returns",
        "block_days": BOOTSTRAP_BLOCK_DAYS, "replications": BOOTSTRAPS, "seed": SEED,
        "primary_mean_daily_return_ci_95": [float(x) for x in np.quantile(bp, [0.025, 0.975])],
        "primary_minus_fresh_gainer_mean_daily_return_ci_95": [float(x) for x in np.quantile(bd, [0.025, 0.975])],
    }


PREFLIGHT_RUN_ID = 38643611024
PREFLIGHT_ARTIFACT_ID = 11934457001
PREFLIGHT_ARTIFACT_SHA256 = "9e20b58052446be9574ac77f4d9d3fef5f1c22e5c7f1dfb5ec94a309e183edfd"
PREFLIGHT_SUMMARY_SHA256 = "0074d5f245fa4323d7370491d320a756c50bcbb6233b3447a2ef62f2cae6c7fc"
PREFLIGHT_HEAD_SHA = "a7da33233e1567db91554d819a39957d084d8fe6"
PREFLIGHT_CANDIDATE_SPEC_SHA256 = "8f561ef21f587dbd1712dd500a891ba58abe891a35c1694e81e2a8ef5bc94186"
PREFLIGHT_ROOT = OUT / "preflight"
EXPECTED_COLUMNS = ["ts_ms", "open", "high", "low", "close", "volume", "quote_volume"]


def evaluate_promotion_gates(primary_base: dict, primary_high: dict, comparator_base: dict,
                             first_half_base: dict, second_half_base: dict,
                             preflight_evidence_valid: bool) -> dict:
    """Evaluate frozen promotion criteria; absolute profitability is distinct from comparator outperformance."""
    def positive(metric: dict, key: str) -> bool:
        value = metric.get(key)
        return value is not None and finite(value) and float(value) > 0.0

    def number(metric: dict, key: str):
        value = metric.get(key)
        return float(value) if value is not None and finite(value) else None

    primary_base_expectancy = number(primary_base, "expectancy_per_trade")
    primary_high_expectancy = number(primary_high, "expectancy_per_trade")
    comparator_expectancy = number(comparator_base, "expectancy_per_trade")
    trade_count = primary_base.get("closed_trade_count")
    gates = {
        "pinned_preflight_evidence_valid": bool(preflight_evidence_valid),
        "at_least_50_primary_final_oos_trades": (
            isinstance(trade_count, (int, float)) and not isinstance(trade_count, bool) and trade_count >= 50
        ),
        "positive_absolute_primary_expectancy_at_base_5bps": (
            primary_base_expectancy is not None and primary_base_expectancy > 0.0
        ),
        "positive_absolute_primary_expectancy_at_highest_20bps": (
            primary_high_expectancy is not None and primary_high_expectancy > 0.0
        ),
        "positive_primary_annualized_sharpe_at_highest_20bps": positive(primary_high, "annualized_sharpe"),
        "primary_expectancy_exceeds_fresh_gainer_at_base_5bps": (
            primary_base_expectancy is not None and comparator_expectancy is not None
            and primary_base_expectancy > comparator_expectancy
        ),
        "positive_primary_net_return_in_both_final_oos_halves_at_5bps": (
            positive(first_half_base, "net_total_return") and positive(second_half_base, "net_total_return")
        ),
    }
    gates["all_predefined_gates_pass"] = all(gates.values())
    return gates


def load_aligned_data() -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex, dict]:
    """Load only the pinned 003 preflight artifact; never refetch or substitute candles."""
    summary_path = PREFLIGHT_ROOT / "preflight_summary.json"
    if not summary_path.is_file():
        raise DataGateBlocked("Pinned Experiment 003 preflight summary is missing")
    summary_bytes = summary_path.read_bytes()
    if sha256_bytes(summary_bytes) != PREFLIGHT_SUMMARY_SHA256:
        raise DataGateBlocked("Preflight summary SHA-256 differs from the preregistered value")
    try:
        summary = json.loads(summary_bytes.decode("utf-8"))
    except Exception as exc:
        raise DataGateBlocked(f"Preflight summary is not valid JSON: {exc}") from exc

    if summary.get("status") != "preflight_pass":
        raise DataGateBlocked(f"Preflight did not pass: {summary.get('status')}")
    if str(summary.get("github_run_id")) != str(PREFLIGHT_RUN_ID):
        raise DataGateBlocked("Preflight run ID does not match the frozen source run")
    if summary.get("github_sha") != PREFLIGHT_HEAD_SHA:
        raise DataGateBlocked("Preflight commit SHA does not match the frozen source commit")
    if summary.get("candidate_spec_sha256") != PREFLIGHT_CANDIDATE_SPEC_SHA256:
        raise DataGateBlocked("Preflight candidate-universe specification SHA-256 mismatch")
    if summary.get("strategy_metrics_emitted") is not False:
        raise DataGateBlocked("Preflight must be data-only and must not contain strategy metrics")
    if summary.get("candidate_symbols") != SYMBOLS:
        raise DataGateBlocked("Preflight candidate symbol list differs from the 24-symbol frozen universe")
    if len(SYMBOLS) != 24 or len(set(SYMBOLS)) != 24:
        raise DataGateBlocked("Frozen universe must contain exactly 24 unique markets")

    live = summary.get("live_instrument_check") or {}
    if live.get("all_candidates_live") is not True or live.get("missing_or_not_live") != []:
        raise DataGateBlocked("Pinned preflight does not establish all 24 markets are currently live")
    reports = summary.get("symbol_reports") or {}
    if set(reports) != set(SYMBOLS):
        raise DataGateBlocked("Preflight does not contain exactly one report per frozen market")

    downloaded: dict[str, pd.DataFrame] = {}
    details = {}
    arrays: dict[str, np.ndarray] = {}
    base_dir = PREFLIGHT_ROOT.resolve()
    for symbol in SYMBOLS:
        meta = reports[symbol]
        expected_rel = "bars/" + symbol.replace("-", "_") + ".csv.gz"
        if meta.get("compressed_data_file") != expected_rel:
            raise DataGateBlocked(f"{symbol}: artifact path differs from the canonical filename")
        path = (PREFLIGHT_ROOT / expected_rel).resolve()
        if base_dir not in path.parents or not path.is_file():
            raise DataGateBlocked(f"{symbol}: expected compressed candle file is missing or escapes artifact root")
        try:
            canonical = gzip.decompress(path.read_bytes())
        except Exception as exc:
            raise DataGateBlocked(f"{symbol}: candle gzip cannot be decompressed: {exc}") from exc
        digest = sha256_bytes(canonical)
        if digest != meta.get("canonical_uncompressed_csv_sha256"):
            raise DataGateBlocked(f"{symbol}: canonical candle SHA-256 does not match pinned preflight manifest")
        frame = pd.read_csv(io.BytesIO(canonical), dtype={"ts_ms": "int64"})
        if frame.columns.tolist() != EXPECTED_COLUMNS:
            raise DataGateBlocked(f"{symbol}: candle columns differ from canonical schema")
        if len(frame) != int(meta.get("confirmed_rows", -1)):
            raise DataGateBlocked(f"{symbol}: row count differs from preflight summary")
        if frame.empty:
            raise DataGateBlocked(f"{symbol}: candle history is empty")
        timestamps = frame["ts_ms"].to_numpy(dtype=np.int64)
        if len(np.unique(timestamps)) != len(timestamps) or not (np.diff(timestamps) > 0).all():
            raise DataGateBlocked(f"{symbol}: duplicate or non-monotonic timestamps")
        if (timestamps % BAR_MS != 0).any():
            raise DataGateBlocked(f"{symbol}: candle timestamps are not aligned to 15 minutes")
        if int(timestamps[0]) != int(meta.get("first_candle_open_ms", -1)):
            raise DataGateBlocked(f"{symbol}: first timestamp differs from preflight summary")
        if int(timestamps[-1]) != int(meta.get("last_confirmed_candle_open_ms", -1)):
            raise DataGateBlocked(f"{symbol}: last timestamp differs from preflight summary")
        vals = frame[["open", "high", "low", "close", "volume", "quote_volume"]].to_numpy(dtype=float)
        if not np.isfinite(vals).all():
            raise DataGateBlocked(f"{symbol}: non-finite OHLCV in pinned candle file")
        if (frame[["open", "high", "low", "close"]] <= 0).any().any():
            raise DataGateBlocked(f"{symbol}: non-positive OHLC in pinned candle file")
        if (frame[["volume", "quote_volume"]] < 0).any().any():
            raise DataGateBlocked(f"{symbol}: negative volume in pinned candle file")
        if (frame["high"] < frame[["open", "low", "close"]].max(axis=1)).any():
            raise DataGateBlocked(f"{symbol}: invalid high-price bounds")
        if (frame["low"] > frame[["open", "high", "close"]].min(axis=1)).any():
            raise DataGateBlocked(f"{symbol}: invalid low-price bounds")
        deltas = np.diff(timestamps)
        missing_count = int(np.maximum(0, (deltas // BAR_MS) - 1).sum())
        missing_fraction = missing_count / max(1, int((timestamps[-1] - timestamps[0]) // BAR_MS + 1))
        max_gap = int(max(0, (deltas.max() // BAR_MS) - 1)) if len(deltas) else 0
        if missing_fraction > MAX_GAP_FRACTION or max_gap > MAX_CONSECUTIVE_GAP_BARS:
            raise DataGateBlocked(f"{symbol}: continuity gates failed (missing={missing_fraction:.6%}, max_gap={max_gap})")
        recorded_fraction = meta.get("missing_fraction")
        if recorded_fraction is not None and abs(float(recorded_fraction) - missing_fraction) > 1e-12:
            raise DataGateBlocked(f"{symbol}: missing-bar fraction differs from source report")
        downloaded[symbol] = frame
        arrays[symbol] = timestamps
        details[symbol] = {
            "symbol": symbol, "rows": len(frame), "first_candle_open_ms": int(timestamps[0]),
            "last_confirmed_candle_open_ms": int(timestamps[-1]), "canonical_uncompressed_csv_sha256": digest,
            "missing_fraction": missing_fraction, "max_consecutive_missing_bars": max_gap,
            "compressed_data_file": expected_rel,
        }

    start_ms = max(int(t[0]) for t in arrays.values())
    end_ms = min(int(t[-1]) for t in arrays.values())
    if start_ms > int(START.timestamp() * 1000) + BAR_MS:
        raise DataGateBlocked("Common panel begins after the frozen requested start")
    if end_ms <= start_ms:
        raise DataGateBlocked("Pinned candle files have no common history")
    span = (end_ms - start_ms) / 86_400_000
    if span < MIN_HISTORY_DAYS:
        raise DataGateBlocked(f"Only {span:.1f} common days; at least {MIN_HISTORY_DAYS} required")
    index = pd.date_range(
        pd.to_datetime(start_ms, unit="ms", utc=True),
        pd.to_datetime(end_ms, unit="ms", utc=True), freq=BAR_DELTA, tz="UTC"
    )
    for symbol, timestamps in arrays.items():
        lo, hi = np.searchsorted(timestamps, [start_ms, end_ms], side="left")
        overlap = timestamps[lo:hi + 1]
        if len(overlap) != len(index) or not np.array_equal(overlap, index.asi8 // 1_000_000):
            raise DataGateBlocked(f"{symbol}: common-grid timestamp alignment check failed")

    recorded_common = summary.get("common_panel") or {}
    if int(recorded_common.get("bars_observed_by_all_symbols", -1)) != len(index):
        raise DataGateBlocked("Common-bar count does not match the pinned preflight summary")
    if int(recorded_common.get("expected_common_grid_bars", -1)) != len(index):
        raise DataGateBlocked("Expected common-grid bar count does not match actual candle files")
    if int(recorded_common.get("missing_joint_timestamps", -1)) != 0:
        raise DataGateBlocked("Preflight summary reports missing joint timestamps")
    if float(recorded_common.get("joint_missing_fraction", 1.0)) != 0.0:
        raise DataGateBlocked("Preflight summary reports nonzero joint missing fraction")

    features = {symbol: compute_features(frame, index) for symbol, frame in downloaded.items()}
    details_summary = {
        "source": "pinned GitHub Actions artifact from the independently reviewed preflight",
        "source_run_id": PREFLIGHT_RUN_ID,
        "source_artifact_id": PREFLIGHT_ARTIFACT_ID,
        "source_artifact_sha256": PREFLIGHT_ARTIFACT_SHA256,
        "source_summary_sha256": PREFLIGHT_SUMMARY_SHA256,
        "source_preflight_commit": PREFLIGHT_HEAD_SHA,
        "source_candidate_spec_sha256": PREFLIGHT_CANDIDATE_SPEC_SHA256,
        "source_status": summary["status"],
        "instrument_preflight": live,
        "symbols": details,
        "requested_start_utc": START.isoformat(),
        "actual_common_start_utc": index[0].isoformat(),
        "actual_common_end_candle_open_utc": index[-1].isoformat(),
        "common_bar_count": len(index),
        "common_calendar_span_days": span,
        "joint_missing_fraction_verified": 0.0,
    }
    return features, index, details_summary


def run_research() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    started = utc_now()
    script_hash = sha256_bytes(Path(__file__).read_bytes())
    spec_hash = sha256_bytes(SPEC_PATH.read_bytes()) if SPEC_PATH.exists() else None
    run_meta = {
        "experiment_id": EXPERIMENT_ID, "started_at_utc": started,
        "github_sha": os.environ.get("GITHUB_SHA"), "github_ref": os.environ.get("GITHUB_REF"),
        "github_run_id": os.environ.get("GITHUB_RUN_ID"), "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "python_version": sys.version, "numpy_version": np.__version__, "pandas_version": pd.__version__,
        "runner_sha256": script_hash, "spec_sha256": spec_hash, "requested_start_utc": START.isoformat(),
        "symbols": SYMBOLS,
    }
    write_json(OUT / "run_manifest.json", run_meta)
    try:
        features, index, data_summary = load_aligned_data()
        write_json(OUT / "data_manifest.json", data_summary)
        signal_sets = leader_signals(features, index)
        n = len(index)
        d_end, v_end = int(math.floor(n * .60)), int(math.floor(n * .80))
        half = v_end + (n - v_end) // 2
        segments = {
            "discovery": (0, d_end), "validation": (d_end, v_end),
            "final_oos": (v_end, n), "final_oos_first_half": (v_end, half),
            "final_oos_second_half": (half, n),
        }
        all_metrics, trades_all, daily_all, oos_returns = {}, [], [], {}
        for variant, signals in signal_sets.items():
            all_metrics[variant] = {}
            for segment, (si, ei) in segments.items():
                if ei - si < 10:
                    raise DataGateBlocked(f"Segment {segment} too short")
                all_metrics[variant][segment] = {}
                for slippage in SLIPPAGE_BPS:
                    sim = simulate_segment(features, index, signals, si, ei, slippage)
                    bm = benchmark_equal_weight(features, si, ei, slippage)
                    metrics, daily = performance_metrics(sim["equity"], bm, index, si, ei, sim["trades"],
                                                         sim["time_in_market"], sim["cancelled_gap_entries"])
                    all_metrics[variant][segment][str(int(slippage))] = metrics
                    for j, trade in enumerate(sim["trades"], 1):
                        trades_all.append({"variant": variant, "segment": segment,
                                           "slippage_bps_each_side": int(slippage), "trade_number": j, **trade})
                    if not daily.empty:
                        daily.insert(0, "variant", variant)
                        daily.insert(1, "segment", segment)
                        daily.insert(2, "slippage_bps_each_side", int(slippage))
                        daily_all.extend(daily.to_dict(orient="records"))
                    if segment == "final_oos" and slippage == SLIPPAGE_BPS[0]:
                        oos_returns[variant] = daily["strategy_daily_return"].to_numpy(float) if not daily.empty else np.array([])

        bootstrap = moving_block_bootstrap(oos_returns.get("primary_normalized_fresh", np.array([])),
                                           oos_returns.get("baseline_fresh_24h_gainer", np.array([])))
        primary = all_metrics["primary_normalized_fresh"]
        gainer = all_metrics["baseline_fresh_24h_gainer"]
        gates = evaluate_promotion_gates(
            primary_base=primary["final_oos"]["5"],
            primary_high=primary["final_oos"]["20"],
            comparator_base=gainer["final_oos"]["5"],
            first_half_base=primary["final_oos_first_half"]["5"],
            second_half_base=primary["final_oos_second_half"]["5"],
            preflight_evidence_valid=(
                data_summary.get("source_run_id") == PREFLIGHT_RUN_ID
                and data_summary.get("source_artifact_sha256") == PREFLIGHT_ARTIFACT_SHA256
                and data_summary.get("joint_missing_fraction_verified") == 0.0
            ),
        )
        passed = gates["all_predefined_gates_pass"]
        result = {
            "experiment_id": EXPERIMENT_ID, "status": "executed_exploratory_backtest",
            "historical_result_is_independent_confirmation": False,
            "started_at_utc": started, "finished_at_utc": utc_now(), "run_manifest": run_meta,
            "data_summary": data_summary,
            "splits": {k: {"start_index": v[0], "end_index_exclusive": v[1], "bars": v[1] - v[0]}
                       for k, v in segments.items()},
            "fixed_parameters": {"fee_bps_each_side": FEE_BPS, "slippage_bps_each_side": SLIPPAGE_BPS,
                                 "freshness_bars": FRESH_BARS, "z_threshold": Z_THRESHOLD,
                                 "atr_period": ATR_PERIOD, "atr_multiplier": ATR_MULTIPLIER,
                                 "volatility_hours": VOL_HOURS, "bootstrap": BOOTSTRAPS,
                                 "bootstrap_block_days": BOOTSTRAP_BLOCK_DAYS, "bootstrap_seed": SEED},
            "metrics": all_metrics, "bootstrap_final_oos_base_cost": bootstrap,
            "promotion_gate": {**gates, "all_predefined_gates_pass": passed,
                               "note": "Passing the fixed gates only allows independent confirmation, not live deployment."},
            "disposition": "eligible_for_independent_confirmation_only" if passed else "not_promoted",
            "limitations": ["Fixed present-day symbol panel is not a point-in-time all-crypto universe.",
                            "Spot results do not establish perpetual-futures performance.",
                            "No parameter grid or OOS-based tuning was performed."],
        }
        write_json(OUT / "summary.json", result)
        pd.DataFrame(trades_all).to_csv(OUT / "trades.csv", index=False, lineterminator="\n")
        pd.DataFrame(daily_all).to_csv(OUT / "daily_equity.csv", index=False, lineterminator="\n")
        lines = [
            f"# {EXPERIMENT_ID}", "", "## Status: EXECUTED — EXPLORATORY ONLY", "",
            f"- Actual common trace: {data_summary['actual_common_start_utc']} to {data_summary['actual_common_end_candle_open_utc']}",
            f"- Common 15-minute bars: {data_summary['common_bar_count']:,}",
            f"- Symbols: {', '.join(SYMBOLS)}",
            f"- Cost model: {FEE_BPS:.0f} bps fee per side; slippage {', '.join(str(int(x)) for x in SLIPPAGE_BPS)} bps per side",
            f"- Disposition: **{result['disposition']}**", "",
            "| Variant | Slippage bps/side | Net return | CAGR | Sharpe | Max drawdown | Trades | Win rate | Expectancy/trade |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for variant in signal_sets:
            for slippage in SLIPPAGE_BPS:
                m = all_metrics[variant]["final_oos"][str(int(slippage))]
                wr = m["win_rate"]
                ex = m["expectancy_per_trade"]
                lines.append(f"| {variant} | {int(slippage)} | {m['net_total_return']:.3%} | {m['net_cagr']:.3%} | "
                             f"{m['annualized_sharpe']:.3f} | {m['maximum_drawdown']:.3%} | {m['closed_trade_count']} | "
                             f"{wr if wr is not None else float('nan'):.3f} | {ex if ex is not None else float('nan'):.3%} |")
        lines += ["", "## Integrity and interpretation", "",
                  "- Leaders are recorded at every completed candle, whether or not a trade filter passes.",
                  "- Signal calculations use completed bars; new trades enter at the following bar open.",
                  "- Stop gaps are filled at the opening price; stops updated at a candle close become active next candle.",
                  "- No missing price is forward-filled. The pinned preflight artifact, coverage, and canonical hashes are recorded in data_manifest.json.",
                  "- The fixed symbol list is not a point-in-time all-crypto universe; results cannot establish a market-wide edge.",
                  "- Experiment 003 was initiated after examining Experiment 001 and is exploratory, not independent statistical confirmation.",
                  "- Positive historical returns alone do not establish alpha.", "", "## Fixed promotion gates", ""]
        lines += [f"- {k}: {v}" for k, v in gates.items()]
        lines += [f"- all_predefined_gates_pass: {passed}", "",
                  f"- runner SHA-256: {script_hash}", f"- spec SHA-256: {spec_hash}",
                  "- Strategy inputs were downloaded from pinned preflight artifact 11934457001; individual canonical candle hashes are rechecked before use.",
                  "- Full metrics and provenance are in summary.json; trade ledger and daily equity are in CSV artifacts."]
        (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {"status": result["status"], "disposition": result["disposition"],
                "common_bars": data_summary["common_bar_count"], "output": str(OUT)}
    except DataGateBlocked as exc:
        block = {"experiment_id": EXPERIMENT_ID, "status": "blocked_data_gate", "timestamp_utc": utc_now(),
                 "reason": str(exc), "no_strategy_metrics_emitted": True, "run_manifest": run_meta}
        write_json(OUT / "data_gate.json", block)
        write_json(OUT / "summary.json", block)
        (OUT / "report.md").write_text(f"# {EXPERIMENT_ID}\n\n## Status: BLOCKED — DATA GATE\n\n"
                                       f"**Reason:** {exc}\n\nNo strategy performance metrics were emitted. "
                                       "This is not a positive or negative strategy finding. Do not substitute daily "
                                       "data for the preregistered intraday test.\n", encoding="utf-8")
        return block
    except Exception as exc:
        error = {"experiment_id": EXPERIMENT_ID, "status": "execution_error", "timestamp_utc": utc_now(),
                 "error": str(exc), "traceback": traceback.format_exc(), "no_valid_strategy_conclusion": True,
                 "run_manifest": run_meta}
        write_json(OUT / "execution_error.json", error)
        (OUT / "report.md").write_text(f"# {EXPERIMENT_ID}\n\n## Status: EXECUTION ERROR\n\n{exc}\n\n"
                                       "No valid strategy conclusion may be drawn from this run.\n", encoding="utf-8")
        raise


if __name__ == "__main__":
    outcome = run_research()
    print(json.dumps(outcome, indent=2, sort_keys=True))
