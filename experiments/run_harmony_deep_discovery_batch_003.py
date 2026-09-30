import csv
import hashlib
import io
import json
import math
import statistics
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

SYMBOL = "BTCUSDT"
FUT_ROOT = Path("data/cache/binance/futures_um/deep_history_2019")
OUT = Path("artifacts/HARMONY-DEEP-DISCOVERY-BATCH-003")
START = date(2019, 1, 1)
END = date(2025, 10, 31)
OOS_START = "2024-05-22"
FEE = 0.0006
SLIP = 0.0005
STABLECOIN_URL = "https://stablecoins.llama.fi/stablecoincharts/all"
ALFRED_URL = "https://alfred.stlouisfed.org/graph/alfredgraph.csv"


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def fetch(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": "Harmony/DEEP-DISCOVERY-BATCH-003"})
    with urlopen(req, timeout=120) as response:
        return response.read()


def load_btc_close():
    prices = {}
    base = FUT_ROOT / "klines" / SYMBOL / "1d"
    for path in sorted(base.glob(f"{SYMBOL}-1d-*.zip")):
        import zipfile
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise ValueError(f"archive CRC failure: {path}")
            names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
            if len(names) != 1:
                raise ValueError(f"unexpected archive shape: {path}")
            for row in csv.reader(archive.read(names[0]).decode("utf-8").splitlines()):
                if row and row[0].isdigit():
                    ts = int(row[0])
                    if ts >= 100_000_000_000_000:
                        ts /= 1_000_000
                    else:
                        ts /= 1_000
                    d = datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
                    prices[d] = float(row[4])
    dates = sorted(d for d in prices if d <= END.isoformat())
    if not dates:
        raise RuntimeError("empty BTC futures history")
    return prices, dates


def load_funding():
    path = FUT_ROOT / "funding_gateway" / f"{SYMBOL}-2019-2025-10.json"
    rows = json.loads(path.read_text())
    out = {}
    for row in rows:
        d = datetime.fromtimestamp(
            int(row["fundingTime"]) / 1000.0, timezone.utc
        ).date().isoformat()
        out.setdefault(d, []).append(float(row["fundingRate"]))
    return out


def parse_stablecoin_series(raw: bytes):
    payload = json.loads(raw.decode("utf-8"))
    rows = []
    for row in payload:
        if "date" not in row or "totalCirculatingUSD" not in row:
            continue
        value = row["totalCirculatingUSD"]
        if isinstance(value, dict):
            value = value.get("peggedUSD")
        if value is None:
            continue
        rows.append((datetime.fromtimestamp(int(row["date"]), timezone.utc).date().isoformat(), float(value)))
    rows.sort()
    dedup = {}
    for d, value in rows:
        if value > 0:
            dedup[d] = value
    return [(d, dedup[d]) for d in sorted(dedup)]


def alfred_vintage_url(vintage_date: str) -> str:
    return (
        f"{ALFRED_URL}?id=M2SL&cosd=2019-01-01&coed={END.isoformat()}"
        f"&vintage_date={vintage_date}"
    )


def parse_alfred_csv(raw: bytes):
    reader = csv.reader(io.StringIO(raw.decode("utf-8-sig")))
    rows = list(reader)
    if not rows:
        return []
    header = rows[0]
    if not header or header[0] != "observation_date" or len(header) < 2:
        raise ValueError(f"unexpected ALFRED header: {header}")
    rows_out = []
    for row in rows[1:]:
        if len(row) < 2:
            continue
        obs = row[0]
        value = row[1]
        if not obs or value in (None, "", "."):
            continue
        rows_out.append((obs, float(value)))
    rows_out.sort()
    return rows_out


def month_ends(start: date, end: date):
    out = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(date(y, m, monthrange(y, m)[1]))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def point_in_time_m2_signals(fetcher=fetch):
    manifest = []
    signals = []
    for month_end in month_ends(START.replace(day=1), END):
        vintage = month_end.isoformat()
        raw = fetcher(alfred_vintage_url(vintage))
        rows = parse_alfred_csv(raw)
        if len(rows) < 2:
            continue
        latest_date, latest_value = rows[-1]
        prior_date, prior_value = rows[-2]
        growth = latest_value / prior_value - 1.0
        signals.append({
            "vintage_date": vintage,
            "latest_observation_date": latest_date,
            "prior_observation_date": prior_date,
            "growth": growth,
        })
        manifest.append({
            "vintage_date": vintage,
            "url": alfred_vintage_url(vintage),
            "sha256": sha256_bytes(raw),
            "bytes": len(raw),
            "observations": len(rows),
        })
    return signals, manifest


def signal_map_from_stablecoin(series, btc_dates):
    by_date = dict(series)
    eligible = []
    for d in btc_dates:
        current = date.fromisoformat(d) - timedelta(days=1)
        prior = current - timedelta(days=7)
        if current.isoformat() in by_date and prior.isoformat() in by_date:
            eligible.append(d)
    out = {}
    for d in eligible[::7]:
        current = date.fromisoformat(d) - timedelta(days=1)
        prior = current - timedelta(days=7)
        change = by_date[current.isoformat()] / by_date[prior.isoformat()] - 1.0
        out[d] = 1.0 if change > 0 else -1.0 if change < 0 else 0.0
    return out


def signal_map_from_m2(signals, btc_dates):
    out = {}
    ordered_dates = [date.fromisoformat(d) for d in btc_dates]
    for row in signals:
        vintage = date.fromisoformat(row["vintage_date"])
        activation_index = next((i for i, d in enumerate(ordered_dates) if d > vintage), None)
        if activation_index is None:
            continue
        activation_date = ordered_dates[activation_index].isoformat()
        out[activation_date] = 1.0 if row["growth"] > 0 else 0.0
    return out


def metrics(curve):
    curve = list(curve)
    if not curve or curve[0] <= 0:
        raise ValueError("invalid equity curve")
    rr = [curve[i] / curve[i - 1] - 1.0 for i in range(1, len(curve))]
    sd = statistics.stdev(rr) if len(rr) > 1 else 0.0
    sharpe = statistics.mean(rr) / sd * math.sqrt(365.25) if sd else 0.0
    peak = curve[0]
    mdd = 0.0
    for value in curve:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    years = max((len(curve) - 1) / 365.25, 1e-12)
    return {
        "final_equity": curve[-1],
        "cumulative_return": curve[-1] - 1.0,
        "cagr": curve[-1] ** (1.0 / years) - 1.0,
        "sharpe": sharpe,
        "max_drawdown": mdd,
        "observations": len(curve),
    }


def segment_metrics(dates, equity, start_date):
    selected = [(i, d, e) for i, (d, e) in enumerate(zip(dates, equity)) if d >= start_date]
    if not selected:
        return None
    first_index = selected[0][0]
    base = 1.0 if first_index == 0 else equity[first_index - 1]
    curve = [1.0] + [e / base for _i, _d, e in selected]
    result = metrics(curve)
    result.update({
        "start": selected[0][1],
        "end": selected[-1][1],
        "observations": len(selected),
    })
    return result


def halves_metrics(dates, equity, start_date):
    selected = [(i, d, e) for i, (d, e) in enumerate(zip(dates, equity)) if d >= start_date]
    if len(selected) < 4:
        return None
    mid = len(selected) // 2
    def run(part):
        first_index = part[0][0]
        base = 1.0 if first_index == 0 else equity[first_index - 1]
        result = metrics([1.0] + [e / base for _i, _d, e in part])
        result.update({
            "start": part[0][1],
            "end": part[-1][1],
            "observations": len(part),
        })
        return result
    return {
        "first_half": run(selected[:mid]),
        "second_half": run(selected[mid:]),
        "split": {
            "first_half_end": selected[mid - 1][1],
            "second_half_start": selected[mid][1],
        },
    }


def simulate(dates, close, funding, signal_map, mode="signal", cost_mult=1.0):
    eq = 1.0
    pos = 0.0
    curve = []
    turnover = 0.0
    funding_sum = 0.0

    for i, d in enumerate(dates):
        for rate in funding.get(d, []):
            pnl = -pos * rate
            eq *= 1.0 + pnl
            funding_sum += pnl

        if i > 0:
            pd = dates[i - 1]
            eq *= 1.0 + pos * (close[d] / close[pd] - 1.0)

        if d in signal_map:
            target = signal_map[d]
            delta = abs(target - pos)
            turnover += delta / 2.0
            eq *= max(0.0, 1.0 - (FEE + SLIP) * cost_mult * delta)
            pos = target

        curve.append(eq)

    eq *= max(0.0, 1.0 - (FEE + SLIP) * cost_mult * abs(pos))
    curve[-1] = eq
    return {
        "metrics": metrics(curve),
        "oos": segment_metrics(dates, curve, OOS_START),
        "oos_halves": halves_metrics(dates, curve, OOS_START),
        "turnover": turnover,
        "funding_pnl_sum": funding_sum,
        "equity": curve,
    }


def run_candidate(name, dates, close, funding, signal_map):
    runs = {}
    for mult in (1.0, 1.5, 2.0):
        runs[f"{mult:.1f}x"] = simulate(dates, close, funding, signal_map, cost_mult=mult)
    base = runs["1.0x"]
    return {
        "base": {k: v for k, v in base.items() if k != "equity"},
        "cost_stress": {
            k: {
                "metrics": v["metrics"],
                "oos": v["oos"],
                "oos_halves": v["oos_halves"],
            }
            for k, v in runs.items()
        },
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    close, btc_dates = load_btc_close()
    funding = load_funding()

    stable_raw = fetch(STABLECOIN_URL)
    stable_series = parse_stablecoin_series(stable_raw)
    stable_signals = signal_map_from_stablecoin(stable_series, btc_dates)

    m2_signals, m2_manifest = point_in_time_m2_signals()
    m2_signal_map = signal_map_from_m2(m2_signals, btc_dates)

    stable_result = run_candidate("HARMONY-FIN-0019", btc_dates, close, funding, stable_signals)
    m2_result = run_candidate("HARMONY-FIN-0020", btc_dates, close, funding, m2_signal_map)

    benchmark = {}
    bh_signal = {btc_dates[0]: 1.0}
    bh = simulate(btc_dates, close, funding, bh_signal)
    benchmark["BTCUSDT_buy_and_hold"] = {
        "metrics": bh["metrics"],
        "oos": bh["oos"],
        "oos_halves": bh["oos_halves"],
    }

    input_manifest = {
        "batch_id": "HARMONY-DEEP-DISCOVERY-BATCH-003",
        "futures_cache_key": "harmony-binance-um-deep-history-2019-2025-10-v1-36777989764",
        "futures_dates": {"start": btc_dates[0], "end": btc_dates[-1], "observations": len(btc_dates)},
        "stablecoin_source": STABLECOIN_URL,
        "stablecoin_snapshot_sha256": sha256_bytes(stable_raw),
        "stablecoin_snapshot_bytes": len(stable_raw),
        "stablecoin_observations": len(stable_series),
        "alfred_vintage_manifest": m2_manifest,
        "selection_end": END.isoformat(),
        "oos_start": OOS_START,
        "github_sha": __import__("os").environ.get("GITHUB_SHA"),
    }
    manifest_bytes = json.dumps(input_manifest, sort_keys=True, indent=2).encode() + b"\n"
    manifest_sha = sha256_bytes(manifest_bytes)
    (OUT / "input-manifest.json").write_bytes(manifest_bytes)

    payload = {
        "batch_id": "HARMONY-DEEP-DISCOVERY-BATCH-003",
        "input_manifest_sha256": manifest_sha,
        "candidates": {
            "HARMONY-FIN-0019": stable_result,
            "HARMONY-FIN-0020": m2_result,
        },
        "benchmarks": benchmark,
        "signal_diagnostics": {
            "HARMONY-FIN-0019": {
                "stablecoin_observations": len(stable_series),
                "signal_observations": len(stable_signals),
            },
            "HARMONY-FIN-0020": {
                "alfred_vintages": len(m2_manifest),
                "signal_observations": len(m2_signal_map),
            },
        },
        "integrity": {
            "holdout_access": False,
            "parameter_search": False,
            "universe_search": False,
            "candidate_mutation": False,
            "point_in_time_macro_vintages": True,
            "stablecoin_provider_snapshot_fingerprinted": True,
        },
    }

    raw = json.dumps(payload, sort_keys=True, indent=2).encode() + b"\n"
    result_sha = sha256_bytes(raw)
    (OUT / "HARMONY-DEEP-DISCOVERY-BATCH-003-RESULT.json").write_bytes(raw)
    (OUT / "HARMONY-DEEP-DISCOVERY-BATCH-003-SUMMARY.json").write_text(
        json.dumps({
            "batch_id": payload["batch_id"],
            "result_sha256": result_sha,
            "candidates": {
                name: data["base"]
                for name, data in payload["candidates"].items()
            },
            "signal_diagnostics": payload["signal_diagnostics"],
            "holdout_access": False,
        }, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"batch_id": payload["batch_id"], "result_sha256": result_sha}, indent=2))


if __name__ == "__main__":
    main()
