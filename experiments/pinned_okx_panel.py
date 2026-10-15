#!/usr/bin/env python3
"""Authenticated source loader for the preregistered relative-spread screen."""
from __future__ import annotations

import gzip, hashlib, io, json, math, os, sys, traceback
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

EXPERIMENT_ID = "HARMONY-EXTREME-RELATIVE-SPREAD-001"
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
SPEC_PATH = ROOT / "experiments" / "HARMONY-EXTREME-RELATIVE-SPREAD-001.yaml"


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


