#!/usr/bin/env python3
"""Preregistered exploratory backtest: fresh crypto acceleration leader + ATR trail."""
from __future__ import annotations

import hashlib, json, math, os, sys, time, traceback
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

EXPERIMENT_ID = "HARMONY-ACCEL-LEADER-002"
BASE_URL = "https://www.okx.com"
START = pd.Timestamp("2023-01-01T00:00:00Z")
BAR_DELTA = pd.Timedelta(minutes=15)
BAR_MS = 900_000
SYMBOLS = ["BTC-USDT", "ETH-USDT", "LTC-USDT", "XRP-USDT",
           "BNB-USDT", "BCH-USDT", "ADA-USDT", "DOGE-USDT"]
FEE_BPS, SLIPPAGE_BPS = 5.0, [5.0, 10.0, 20.0]
ATR_PERIOD, ATR_MULTIPLIER = 14, 3.0
FRESH_BARS, Z_THRESHOLD, VOL_HOURS = 96, 1.0, 168
MAX_PAGES, MIN_HISTORY_DAYS = 2200, 730
MAX_GAP_FRACTION, MAX_CONSECUTIVE_GAP_BARS, MAX_TAIL_AGE_HOURS = 0.001, 4, 2.0
BOOTSTRAPS, BOOTSTRAP_BLOCK_DAYS, SEED = 1000, 5, 1401
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / EXPERIMENT_ID
SPEC_PATH = ROOT / "experiments" / "HARMONY-ACCEL-LEADER-002.yaml"


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


def request_json(path: str, params: dict, retries: int = 6) -> dict:
    url = BASE_URL + path + "?" + urlencode(params)
    last_error = None
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": EXPERIMENT_ID + "/1.0", "Accept": "application/json"})
            with urlopen(req, timeout=35) as response:
                raw = response.read()
            obj = json.loads(raw.decode("utf-8"))
            if not isinstance(obj, dict) or str(obj.get("code", "0")) != "0":
                raise RuntimeError("OKX API error: " + str(obj.get("code")) + " " + str(obj.get("msg")))
            return obj
        except Exception as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(min(0.5 * (2 ** attempt), 8.0))
    raise RuntimeError(f"OKX request failed after {retries} attempts: {url}; error={last_error}")


def verify_live_symbols() -> dict:
    payload = request_json("/api/v5/public/instruments", {"instType": "SPOT"})
    live = {str(r.get("instId")) for r in payload.get("data", []) if str(r.get("state", "")).lower() == "live"}
    missing = [s for s in SYMBOLS if s not in live]
    if missing:
        raise DataGateBlocked("Pre-registered spot universe has instruments unavailable as live OKX markets: "
                              + ", ".join(missing) + ". Do not silently replace symbols.")
    return {"status": "passed", "symbols": SYMBOLS, "api_instrument_rows": len(payload.get("data", []))}


def fetch_candles(symbol: str) -> tuple[pd.DataFrame, dict]:
    rows, cursor, pages, prior_oldest, reached_start = [], None, 0, None, False
    began = time.time()
    while pages < MAX_PAGES:
        params = {"instId": symbol, "bar": "15m", "limit": "300"}
        if cursor is not None:
            params["after"] = str(cursor)
        payload = request_json("/api/v5/market/history-candles", params)
        page = payload.get("data", [])
        pages += 1
        if not page:
            reached_start = True
            break
        stamps = []
        for r in page:
            if len(r) < 9:
                raise DataGateBlocked(f"{symbol}: malformed candle row")
            try:
                ts = int(r[0])
                stamps.append(ts)
                if ts >= int(START.timestamp() * 1000) and str(r[8]) == "1":
                    rows.append({"ts_ms": ts, "open": float(r[1]), "high": float(r[2]), "low": float(r[3]),
                                 "close": float(r[4]), "volume": float(r[5]), "quote_volume": float(r[7])})
            except (TypeError, ValueError) as exc:
                raise DataGateBlocked(f"{symbol}: unparsable candle row: {exc}") from exc
        if not stamps:
            reached_start = True
            break
        oldest = min(stamps)
        if oldest <= int(START.timestamp() * 1000):
            reached_start = True
            break
        if prior_oldest is not None and oldest >= prior_oldest:
            raise DataGateBlocked(f"{symbol}: pagination did not move backward")
        prior_oldest, cursor = oldest, oldest - 1
        time.sleep(0.12)
    if not rows:
        raise DataGateBlocked(f"{symbol}: no confirmed candles from requested start")
    if not reached_start:
        raise DataGateBlocked(f"{symbol}: hit {MAX_PAGES} pages before requested history start")
    df = pd.DataFrame(rows).sort_values("ts_ms").reset_index(drop=True)
    if df.ts_ms.duplicated().any() or not (df.ts_ms.diff().dropna() > 0).all():
        raise DataGateBlocked(f"{symbol}: duplicate or non-monotonic candle timestamps")
    if (df.ts_ms % BAR_MS != 0).any():
        raise DataGateBlocked(f"{symbol}: candle timestamp is not aligned to 15 minutes")
    cols = ["open", "high", "low", "close", "volume", "quote_volume"]
    vals = df[cols].to_numpy(dtype=float)
    if not np.isfinite(vals).all():
        raise DataGateBlocked(f"{symbol}: non-finite OHLCV")
    if (df[["open", "high", "low", "close"]] <= 0).any().any() or (df[["volume", "quote_volume"]] < 0).any().any():
        raise DataGateBlocked(f"{symbol}: invalid price or volume")
    if (df.high < df[["open", "low", "close"]].max(axis=1)).any() or (df.low > df[["open", "high", "close"]].min(axis=1)).any():
        raise DataGateBlocked(f"{symbol}: OHLC bounds fail")
    first = pd.to_datetime(int(df.ts_ms.min()), unit="ms", utc=True)
    last = pd.to_datetime(int(df.ts_ms.max()), unit="ms", utc=True)
    if first > START + BAR_DELTA:
        raise DataGateBlocked(f"{symbol}: history starts at {first.isoformat()}, after requested start")
    tail_age = (pd.Timestamp.now(tz="UTC") - (last + BAR_DELTA)).total_seconds() / 3600
    if tail_age > MAX_TAIL_AGE_HOURS:
        raise DataGateBlocked(f"{symbol}: last confirmed candle is stale by {tail_age:.2f} hours")
    grid = pd.date_range(first, last, freq=BAR_DELTA, tz="UTC")
    observed = pd.DatetimeIndex(pd.to_datetime(df.ts_ms, unit="ms", utc=True))
    absent = grid.difference(observed)
    gap_fraction = len(absent) / max(1, len(grid))
    max_gap, run = 0, 0
    absent_set = set(absent.asi8.tolist())
    for ts in grid.asi8:
        if ts in absent_set:
            run += 1
            max_gap = max(max_gap, run)
        else:
            run = 0
    if gap_fraction > MAX_GAP_FRACTION or max_gap > MAX_CONSECUTIVE_GAP_BARS:
        raise DataGateBlocked(f"{symbol}: missing-candle continuity gate failed ({gap_fraction:.6%}, max gap {max_gap})")
    canonical = df.to_csv(index=False, lineterminator="\n", float_format="%.12g").encode("utf-8")
    outpath = OUT / "bars" / (symbol.replace("-", "_") + ".csv.gz")
    outpath.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(outpath, index=False, compression="gzip", lineterminator="\n", float_format="%.12g")
    return df, {
        "symbol": symbol, "pages": pages, "rows": len(df),
        "first_candle_open_utc": first.isoformat(), "last_confirmed_candle_open_utc": last.isoformat(),
        "last_confirmed_bar_age_hours": round(tail_age, 4), "pagination_reached_requested_start": reached_start,
        "missing_candle_count": int(len(absent)), "missing_fraction": float(gap_fraction),
        "max_consecutive_missing_bars": int(max_gap),
        "canonical_uncompressed_csv_sha256": sha256_bytes(canonical),
        "canonical_uncompressed_csv_bytes": len(canonical),
        "compressed_data_file": str(outpath.relative_to(OUT)),
        "download_elapsed_seconds": round(time.time() - began, 2),
    }


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


def load_aligned_data() -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex, dict]:
    instrument_info = verify_live_symbols()
    downloaded, details = {}, {}
    for symbol in SYMBOLS:
        downloaded[symbol], details[symbol] = fetch_candles(symbol)
    firsts = {s: pd.to_datetime(int(d.ts_ms.min()), unit="ms", utc=True) for s, d in downloaded.items()}
    lasts = {s: pd.to_datetime(int(d.ts_ms.max()), unit="ms", utc=True) for s, d in downloaded.items()}
    start, end = max(firsts.values()), min(lasts.values())
    if start > START + BAR_DELTA:
        raise DataGateBlocked(f"Common panel starts after requested date: {start.isoformat()}")
    span = (end - start).total_seconds() / 86400
    if span < MIN_HISTORY_DAYS:
        raise DataGateBlocked(f"Only {span:.1f} common days; at least {MIN_HISTORY_DAYS} required")
    index = pd.date_range(start, end, freq=BAR_DELTA, tz="UTC")
    features = {}
    for symbol, frame in downloaded.items():
        observed = pd.DatetimeIndex(pd.to_datetime(frame.ts_ms, unit="ms", utc=True))
        absent = index.difference(observed)
        if len(absent) / max(1, len(index)) > MAX_GAP_FRACTION:
            raise DataGateBlocked(f"{symbol}: missing candles exceed the shared panel gap allowance")
        features[symbol] = compute_features(frame, index)
    return features, index, {
        "instrument_preflight": instrument_info, "symbols": details,
        "requested_start_utc": START.isoformat(), "actual_common_start_utc": start.isoformat(),
        "actual_common_end_candle_open_utc": end.isoformat(), "common_bar_count": len(index),
        "common_calendar_span_days": span,
    }


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
        gates = {
            "at_least_50_primary_oos_trades": primary["final_oos"]["5"]["closed_trade_count"] >= 50,
            "positive_primary_oos_expectancy_at_20bps_slippage":
                primary["final_oos"]["20"]["expectancy_per_trade"] is not None
                and primary["final_oos"]["20"]["expectancy_per_trade"] > 0,
            "positive_primary_oos_sharpe_at_20bps_slippage":
                primary["final_oos"]["20"]["annualized_sharpe"] > 0,
            "positive_oos_expectancy_vs_fresh_gainer_at_base_cost":
                primary["final_oos"]["5"]["expectancy_per_trade"] is not None
                and gainer["final_oos"]["5"]["expectancy_per_trade"] is not None
                and primary["final_oos"]["5"]["expectancy_per_trade"] > gainer["final_oos"]["5"]["expectancy_per_trade"],
            "positive_oos_net_total_return_both_halves":
                primary["final_oos_first_half"]["5"]["net_total_return"] > 0
                and primary["final_oos_second_half"]["5"]["net_total_return"] > 0,
        }
        passed = all(gates.values())
        result = {
            "experiment_id": EXPERIMENT_ID, "status": "executed_exploratory_backtest",
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
                  "- No missing price is forward-filled. API history, coverage, and hashes are recorded in data_manifest.json.",
                  "- The fixed symbol list is not a complete point-in-time cryptocurrency universe; results cannot establish a market-wide edge.",
                  "- Positive historical returns alone do not establish alpha.", "", "## Fixed promotion gates", ""]
        lines += [f"- {k}: {v}" for k, v in gates.items()]
        lines += [f"- all_predefined_gates_pass: {passed}", "",
                  f"- runner SHA-256: {script_hash}", f"- spec SHA-256: {spec_hash}",
                  "- Normalized source candles are stored under bars/ with canonical uncompressed CSV hashes.",
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
