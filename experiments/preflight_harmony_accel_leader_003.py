#!/usr/bin/env python3
"""Validate candidate OKX spot universe history; does not run a strategy."""
from __future__ import annotations
import bisect, csv, gzip, hashlib, io, json, math, os, sys, time, traceback
from array import array
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

EXPERIMENT_ID = "HARMONY-ACCEL-LEADER-003-UNIVERSE-PREFLIGHT"
BASE_URL = "https://www.okx.com"
START_MS = int(datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
BAR_MS = 900_000
MAX_PAGES = 800
MAX_GAP_FRACTION = 0.001
MAX_CONSECUTIVE_GAP_BARS = 4
MAX_TAIL_AGE_HOURS = 2.0
MIN_COMMON_HISTORY_DAYS = 730
SYMBOLS = [
    "BTC-USDT", "ETH-USDT", "LTC-USDT", "XRP-USDT", "BNB-USDT", "BCH-USDT",
    "ADA-USDT", "DOGE-USDT", "SOL-USDT", "LINK-USDT", "DOT-USDT", "AVAX-USDT",
    "TRX-USDT", "ETC-USDT", "XLM-USDT", "ATOM-USDT", "FIL-USDT", "OP-USDT",
    "UNI-USDT", "AAVE-USDT", "ALGO-USDT", "NEAR-USDT", "MANA-USDT", "SAND-USDT",
]
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / EXPERIMENT_ID


class DataGateBlocked(RuntimeError):
    """A candidate-universe eligibility or integrity gate failed."""


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def check_candidate_symbols(symbols=SYMBOLS):
    if len(symbols) != 24:
        raise DataGateBlocked(f"Candidate universe must contain exactly 24 symbols; found {len(symbols)}")
    if len(set(symbols)) != len(symbols):
        raise DataGateBlocked("Candidate universe contains duplicate symbols")
    if "EOS-USDT" in symbols:
        raise DataGateBlocked("Candidate preflight must not reuse the known-blocked EOS-USDT symbol")


def timestamp_quality(ts_values, start_ms=START_MS, now_ms=None):
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    vals = list(ts_values)
    if not vals:
        raise DataGateBlocked("No confirmed candles from requested start")
    if vals != sorted(vals):
        raise DataGateBlocked("Candle timestamps are not monotonically increasing")
    if len(vals) != len(set(vals)):
        raise DataGateBlocked("Duplicate candle timestamps")
    if any(ts % BAR_MS != 0 for ts in vals):
        raise DataGateBlocked("Candle timestamp is not aligned to 15 minutes")
    first, last = vals[0], vals[-1]
    if first > start_ms + BAR_MS:
        raise DataGateBlocked("History starts after permitted start boundary")
    age_hours = max(0.0, (now_ms - (last + BAR_MS)) / 3_600_000)
    if age_hours > MAX_TAIL_AGE_HOURS:
        raise DataGateBlocked(f"Last confirmed candle is stale by {age_hours:.2f} hours")
    expected = ((last - first) // BAR_MS) + 1
    missing = expected - len(vals)
    max_run = max((max(0, (right - left) // BAR_MS - 1) for left, right in zip(vals, vals[1:])), default=0)
    missing_fraction = missing / max(1, expected)
    if missing_fraction > MAX_GAP_FRACTION or max_run > MAX_CONSECUTIVE_GAP_BARS:
        raise DataGateBlocked(f"Missing-candle continuity gate failed ({missing_fraction:.6%}, max consecutive gap {max_run})")
    return {
        "first_candle_open_ms": first, "last_confirmed_candle_open_ms": last,
        "confirmed_rows": len(vals), "expected_grid_rows": expected,
        "missing_candle_count": missing, "missing_fraction": missing_fraction,
        "max_consecutive_missing_bars": max_run, "last_confirmed_bar_age_hours": round(age_hours, 4),
    }


def request_json(path, params, retries=6):
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


def verify_live_universe():
    payload = request_json("/api/v5/public/instruments", {"instType": "SPOT"})
    rows = payload.get("data", [])
    live = {str(row.get("instId")) for row in rows if str(row.get("state", "")).lower() == "live"}
    missing = [symbol for symbol in SYMBOLS if symbol not in live]
    return {
        "checked_at_utc": utc_now(), "endpoint": "/api/v5/public/instruments?instType=SPOT",
        "api_instrument_rows": len(rows), "candidate_symbol_count": len(SYMBOLS),
        "live_symbol_count": len(SYMBOLS) - len(missing), "all_candidates_live": not missing,
        "missing_or_not_live": missing,
    }



def load_cached_symbol(symbol):
    """Reuse only a complete cached normalized candle file that passes all current checks."""
    cached_path = OUT / "bars" / (symbol.replace("-", "_") + ".csv.gz")
    if not cached_path.exists():
        return None
    try:
        with gzip.open(cached_path, "rt", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            required = ["ts_ms", "open", "high", "low", "close", "volume", "quote_volume"]
            if reader.fieldnames != required:
                raise ValueError("cached candle header differs from the canonical schema")
            rows = []
            for record in reader:
                rows.append((
                    int(record["ts_ms"]), float(record["open"]), float(record["high"]),
                    float(record["low"]), float(record["close"]), float(record["volume"]),
                    float(record["quote_volume"])
                ))
        timestamps = [row[0] for row in rows]
        quality = timestamp_quality(timestamps)
        for row in rows:
            ts, opn, high, low, close, volume, quote_volume = row
            values = (opn, high, low, close, volume, quote_volume)
            if not all(math.isfinite(value) for value in values):
                raise ValueError("cached OHLCV contains non-finite values")
            if min(opn, high, low, close) <= 0 or volume < 0 or quote_volume < 0:
                raise ValueError("cached OHLCV contains invalid prices or volume")
            if high < max(opn, low, close) or low > min(opn, high, close):
                raise ValueError("cached OHLC bounds fail")
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\\n")
        writer.writerow(["ts_ms", "open", "high", "low", "close", "volume", "quote_volume"])
        for row in rows:
            writer.writerow([row[0], *(format(value, ".12g") for value in row[1:])])
        canonical = stream.getvalue().encode("utf-8")
        source_run = os.environ.get("PREFLIGHT_CACHE_SOURCE_RUN_ID")
        source_artifact = os.environ.get("PREFLIGHT_CACHE_SOURCE_ARTIFACT_ID")
        summary = {
            "symbol": symbol, "pages": 0, **quality,
            "first_candle_open_utc": datetime.fromtimestamp(quality["first_candle_open_ms"] / 1000, timezone.utc).isoformat(),
            "last_confirmed_candle_open_utc": datetime.fromtimestamp(quality["last_confirmed_candle_open_ms"] / 1000, timezone.utc).isoformat(),
            "canonical_uncompressed_csv_sha256": sha256_bytes(canonical),
            "canonical_uncompressed_csv_bytes": len(canonical),
            "compressed_data_file": str(cached_path.relative_to(OUT)),
            "cache_reused": True,
            "cache_source_run_id": source_run,
            "cache_source_artifact_id": source_artifact,
            "cache_revalidated_at_utc": utc_now(),
        }
        print(f"[preflight] revalidated cached {symbol} from run {source_run or 'unspecified'}", flush=True)
        return array("q", timestamps), summary
    except Exception as exc:
        # Invalid or stale cache is never used; fetch the same symbol again from OKX.
        print(f"[preflight] discarding invalid cache for {symbol}: {exc}", file=sys.stderr, flush=True)
        cached_path.unlink(missing_ok=True)
        return None


def fetch_symbol(symbol):
    cached = load_cached_symbol(symbol)
    if cached is not None:
        return cached
    rows, cursor, previous_oldest, reached_start, pages = [], None, None, False, 0
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
        page_timestamps = []
        for raw in page:
            if len(raw) < 9:
                raise DataGateBlocked(f"{symbol}: malformed candle row")
            try:
                ts = int(raw[0])
                page_timestamps.append(ts)
                if ts >= START_MS and str(raw[8]) == "1":
                    rows.append((ts, float(raw[1]), float(raw[2]), float(raw[3]), float(raw[4]), float(raw[5]), float(raw[7])))
            except (TypeError, ValueError) as exc:
                raise DataGateBlocked(f"{symbol}: unparsable candle row: {exc}") from exc
        if not page_timestamps:
            reached_start = True
            break
        oldest = min(page_timestamps)
        if oldest <= START_MS:
            reached_start = True
            break
        if previous_oldest is not None and oldest >= previous_oldest:
            raise DataGateBlocked(f"{symbol}: pagination did not move backward")
        previous_oldest, cursor = oldest, oldest - 1
        time.sleep(0.125)
    if not reached_start:
        raise DataGateBlocked(f"{symbol}: page ceiling {MAX_PAGES} reached before requested history start")
    rows.sort(key=lambda row: row[0])
    timestamps = [row[0] for row in rows]
    quality = timestamp_quality(timestamps)
    for row in rows:
        ts, opn, high, low, close, volume, quote_volume = row
        values = (opn, high, low, close, volume, quote_volume)
        if not all(math.isfinite(value) for value in values):
            raise DataGateBlocked(f"{symbol}: non-finite OHLCV")
        if min(opn, high, low, close) <= 0 or volume < 0 or quote_volume < 0:
            raise DataGateBlocked(f"{symbol}: invalid prices or volume")
        if high < max(opn, low, close) or low > min(opn, high, close):
            raise DataGateBlocked(f"{symbol}: OHLC bounds fail")
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["ts_ms", "open", "high", "low", "close", "volume", "quote_volume"])
    for row in rows:
        writer.writerow([row[0], *(format(value, ".12g") for value in row[1:])])
    canonical = stream.getvalue().encode("utf-8")
    bars_dir = OUT / "bars"
    bars_dir.mkdir(parents=True, exist_ok=True)
    compressed_path = bars_dir / (symbol.replace("-", "_") + ".csv.gz")
    with open(compressed_path, "wb") as raw_file:
        with gzip.GzipFile(fileobj=raw_file, mode="wb", compresslevel=6, mtime=0) as gz:
            gz.write(canonical)
    summary = {
        "symbol": symbol, "pages": pages, **quality,
        "first_candle_open_utc": datetime.fromtimestamp(quality["first_candle_open_ms"] / 1000, timezone.utc).isoformat(),
        "last_confirmed_candle_open_utc": datetime.fromtimestamp(quality["last_confirmed_candle_open_ms"] / 1000, timezone.utc).isoformat(),
        "canonical_uncompressed_csv_sha256": sha256_bytes(canonical),
        "canonical_uncompressed_csv_bytes": len(canonical),
        "compressed_data_file": str(compressed_path.relative_to(OUT)),
        "download_elapsed_seconds": round(time.time() - began, 2),
    }
    return array("q", timestamps), summary


def enforce_joint_coverage(observed_count, expected_count, max_missing_fraction=MAX_GAP_FRACTION):
    if expected_count <= 0 or observed_count < 0 or observed_count > expected_count:
        raise DataGateBlocked("Invalid joint timestamp coverage counts")
    missing_fraction = (expected_count - observed_count) / expected_count
    if missing_fraction > max_missing_fraction:
        raise DataGateBlocked(
            f"Joint common-panel coverage gate failed ({missing_fraction:.6%} missing; "
            f"maximum {max_missing_fraction:.6%})"
        )
    return missing_fraction


def assess_common_panel(timestamp_arrays, symbol_reports):
    first = max(info["first_candle_open_ms"] for info in symbol_reports.values())
    last = min(info["last_confirmed_candle_open_ms"] for info in symbol_reports.values())
    if first > START_MS + BAR_MS:
        raise DataGateBlocked("Common panel starts after requested date boundary")
    if last <= first:
        raise DataGateBlocked("No overlapping confirmed history across candidate symbols")
    common_days = (last - first + BAR_MS) / 86_400_000
    if common_days < MIN_COMMON_HISTORY_DAYS:
        raise DataGateBlocked(f"Only {common_days:.1f} common days; at least {MIN_COMMON_HISTORY_DAYS} required")
    common_observed = None
    expected = ((last - first) // BAR_MS) + 1
    for symbol, stamps in timestamp_arrays.items():
        i0, i1 = bisect.bisect_left(stamps, first), bisect.bisect_right(stamps, last)
        inside = stamps[i0:i1]
        missing = expected - len(inside)
        max_run = max((max(0, (right - left) // BAR_MS - 1) for left, right in zip(inside, inside[1:])), default=0)
        fraction = missing / max(1, expected)
        if fraction > MAX_GAP_FRACTION or max_run > MAX_CONSECUTIVE_GAP_BARS:
            raise DataGateBlocked(f"{symbol}: shared-panel coverage gate failed ({fraction:.6%}, max gap {max_run})")
        if common_observed is None:
            common_observed = set(inside)
        else:
            common_observed.intersection_update(inside)
    joint = len(common_observed or set())
    joint_missing_fraction = enforce_joint_coverage(joint, expected, MAX_GAP_FRACTION)
    return {
        "common_start_utc": datetime.fromtimestamp(first / 1000, timezone.utc).isoformat(),
        "common_end_candle_open_utc": datetime.fromtimestamp(last / 1000, timezone.utc).isoformat(),
        "common_calendar_span_days": round(common_days, 4), "expected_common_grid_bars": expected,
        "bars_observed_by_all_symbols": joint, "joint_timestamp_coverage_fraction": round(1.0 - joint_missing_fraction, 8),
        "joint_missing_fraction": joint_missing_fraction, "missing_joint_timestamps": expected - joint,
        "shared_panel_gap_fraction_max": MAX_GAP_FRACTION,
        "max_consecutive_gap_bars": MAX_CONSECUTIVE_GAP_BARS,
    }


def write_report(summary):
    OUT.mkdir(parents=True, exist_ok=True)
    write_json(OUT / "preflight_summary.json", summary)
    lines = [
        f"# {EXPERIMENT_ID}", "", f"## Outcome: {summary.get('status', 'unknown').upper()}", "",
        "Data-universe preflight only; no signal simulation or strategy-performance metrics were run.", "",
        f"- Candidate symbols: {len(SYMBOLS)}", f"- Panel: {', '.join(SYMBOLS)}",
        f"- GitHub run: {summary.get('github_run_id')}", f"- Commit: {summary.get('github_sha')}",
        f"- Runner SHA-256: {summary.get('runner_sha256')}",
        f"- Candidate specification SHA-256: {summary.get('candidate_spec_sha256')}", "",
    ]
    if summary.get("reason"):
        lines += [f"**Reason:** {summary['reason']}", ""]
    if summary.get("live_instrument_check") is not None:
        lines += ["## Live-instrument check", "", "~~~json", json.dumps(summary["live_instrument_check"], indent=2, sort_keys=True), "~~~", ""]
    if summary.get("common_panel") is not None:
        lines += ["## Common historical panel", "", "~~~json", json.dumps(summary["common_panel"], indent=2, sort_keys=True), "~~~", ""]
    if summary.get("symbol_reports"):
        lines += ["## Per-symbol history and integrity", "",
                  "| Symbol | Rows | First UTC | Last UTC | Missing fraction | Max gap bars | Canonical SHA-256 |",
                  "|---|---:|---|---|---:|---:|---|"]
        for symbol, info in summary["symbol_reports"].items():
            lines.append(f"| {symbol} | {info.get('confirmed_rows', 0)} | {info.get('first_candle_open_utc', '')} | "
                         f"{info.get('last_confirmed_candle_open_utc', '')} | {info.get('missing_fraction', '')} | "
                         f"{info.get('max_consecutive_missing_bars', '')} | {info.get('canonical_uncompressed_csv_sha256', '')} |")
        lines.append("")
    lines += ["## Interpretation", "",
              "- preflight_pass means only that this exact candidate panel passed data-eligibility checks.",
              "- It does not mean Experiment 003 is preregistered or that any strategy is profitable.",
              "- Candidate changes after a failure require a new recorded preflight revision.",
              "- The present-day fixed panel retains survivorship and point-in-time-universe limitations.", ""]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_preflight():
    OUT.mkdir(parents=True, exist_ok=True)
    spec_path = ROOT / "experiments" / "HARMONY-ACCEL-LEADER-003-candidate-universe.yaml"
    summary = {
        "experiment_id": EXPERIMENT_ID, "status": "running", "started_at_utc": utc_now(),
        "github_sha": os.environ.get("GITHUB_SHA"), "github_ref": os.environ.get("GITHUB_REF"),
        "github_run_id": os.environ.get("GITHUB_RUN_ID"), "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "requested_start_utc": "2023-01-01T00:00:00Z", "bar": "15m", "candidate_symbols": SYMBOLS,
        "runner_sha256": sha256_bytes(Path(__file__).read_bytes()),
        "candidate_spec_sha256": sha256_bytes(spec_path.read_bytes()) if spec_path.exists() else None,
        "symbol_reports": {}, "data_artifacts_preserved": True, "strategy_metrics_emitted": False,
        "resume_seed": {
            "source_run_id": os.environ.get("PREFLIGHT_CACHE_SOURCE_RUN_ID"),
            "source_artifact_id": os.environ.get("PREFLIGHT_CACHE_SOURCE_ARTIFACT_ID"),
            "reused_symbols": [],
        },
    }
    try:
        check_candidate_symbols()
        live = verify_live_universe()
        summary["live_instrument_check"] = live
        if not live["all_candidates_live"]:
            summary["status"] = "blocked_data_gate"
            summary["reason"] = "Candidate universe includes symbols not live on OKX spot: " + ", ".join(live["missing_or_not_live"])
        else:
            arrays, blockers = {}, []
            for position, symbol in enumerate(SYMBOLS, 1):
                print(f"[preflight] {position}/{len(SYMBOLS)} fetching and validating {symbol}", flush=True)
                try:
                    timestamps, info = fetch_symbol(symbol)
                    arrays[symbol] = timestamps
                    summary["symbol_reports"][symbol] = info
                    if info.get("cache_reused"):
                        summary["resume_seed"]["reused_symbols"].append(symbol)
                except DataGateBlocked as exc:
                    blockers.append(f"{symbol}: {exc}")
                    summary["symbol_reports"][symbol] = {"symbol": symbol, "status": "blocked_data_gate", "reason": str(exc)}
                write_json(OUT / "preflight_checkpoint.json", {
                    "experiment_id": EXPERIMENT_ID,
                    "status": "running",
                    "updated_at_utc": utc_now(),
                    "github_sha": summary.get("github_sha"),
                    "candidate_symbols": SYMBOLS,
                    "completed_symbol_count": len(summary["symbol_reports"]),
                    "symbol_reports": summary["symbol_reports"],
                    "resume_seed": summary["resume_seed"],
                    "strategy_metrics_emitted": False,
                })
            if blockers:
                summary["status"], summary["reason"] = "blocked_data_gate", "; ".join(blockers)
            else:
                summary["common_panel"] = assess_common_panel(arrays, summary["symbol_reports"])
                summary["status"], summary["reason"] = "preflight_pass", None
    except DataGateBlocked as exc:
        summary["status"], summary["reason"] = "blocked_data_gate", str(exc)
    except Exception as exc:
        summary["status"], summary["reason"] = "technical_failure", str(exc)
        summary["traceback"] = traceback.format_exc()
    summary["finished_at_utc"] = utc_now()
    write_report(summary)
    print(json.dumps(summary, indent=2, sort_keys=True), flush=True)
    return summary


if __name__ == "__main__":
    result = run_preflight()
    sys.exit(2 if result["status"] == "technical_failure" else 0)
