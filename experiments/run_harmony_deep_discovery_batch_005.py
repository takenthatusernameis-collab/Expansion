import csv
import hashlib
import json
import math
import statistics
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

FUT_ROOT = Path("data/cache/binance/futures_um/deep_history_2019")
OUT = Path("artifacts/HARMONY-DEEP-DISCOVERY-BATCH-005")
SYMBOL = "BTCUSDT"
END = "2025-10-31"
OOS_START = "2024-05-22"
FEE = 0.0006
SLIP = 0.0005

def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()

def parse_zip_rows(path):
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise RuntimeError(f"unexpected archive members: {path}")
        return list(csv.reader(z.open(names[0]).read().decode("utf-8").splitlines()))

def load_price_and_funding():
    px = {}
    funding = {}
    kroot = FUT_ROOT / "klines" / SYMBOL / "1d"
    for path in sorted(kroot.glob(f"{SYMBOL}-1d-*.zip")):
        for row in parse_zip_rows(path):
            if row and row[0].isdigit():
                d = datetime.fromtimestamp(int(row[0]) / 1000.0, timezone.utc).date().isoformat()
                px[d] = float(row[4])
    fpath = FUT_ROOT / "funding_gateway" / f"{SYMBOL}-2019-2025-10.json"
    for row in json.loads(fpath.read_text()):
        d = datetime.fromtimestamp(int(row["fundingTime"]) / 1000.0, timezone.utc).date().isoformat()
        funding.setdefault(d, []).append(float(row["fundingRate"]))
    dates = sorted(d for d in px if d <= END)
    if not dates or dates[0] != "2020-07-10" or dates[-1] != END:
        raise RuntimeError(f"unexpected BTC panel: {dates[:1]}..{dates[-1:]}")
    return dates, px, funding

def fetch_chart(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Harmony/DEEP-DISCOVERY-BATCH-005"})
    raw = urllib.request.urlopen(req, timeout=120).read()
    payload = json.loads(raw.decode("utf-8"))
    values = payload.get("values")
    if not isinstance(values, list) or len(values) < 500:
        raise RuntimeError(f"unexpected chart payload: {url}")
    series = {}
    for row in values:
        if not isinstance(row, dict) or "x" not in row or "y" not in row:
            continue
        d = datetime.fromtimestamp(int(row["x"]), timezone.utc).date().isoformat()
        y = float(row["y"])
        if math.isfinite(y):
            series[d] = y
    if len(series) < 500:
        raise RuntimeError(f"insufficient usable chart rows: {url}")
    return raw, series

def rolling_mean(values, dates, end_exclusive, window):
    if end_exclusive < window:
        return None
    segment = [values[dates[i]] for i in range(end_exclusive - window, end_exclusive)]
    return sum(segment) / window

def build_signal_map(dates, indicator, smooth_window=1, recent_window=30, rebalance_every=7):
    if smooth_window > 1:
        smoothed = {}
        for i, d in enumerate(dates):
            m = rolling_mean(indicator, dates, i + 1, smooth_window)
            if m is not None:
                smoothed[d] = m
    else:
        smoothed = dict(indicator)

    usable = [d for d in dates if d in smoothed]
    signals = {}
    for j in range(2 * recent_window, len(usable)):
        if (j - 2 * recent_window) % rebalance_every != 0:
            continue
        recent = sum(smoothed[usable[k]] for k in range(j - recent_window, j)) / recent_window
        prior = sum(smoothed[usable[k]] for k in range(j - 2 * recent_window, j - recent_window)) / recent_window
        signals[usable[j]] = 1.0 if recent > prior else 0.0
    return signals

def metrics(curve):
    if len(curve) < 2:
        raise RuntimeError("curve too short")
    rr = [curve[i] / curve[i - 1] - 1.0 for i in range(1, len(curve))]
    sd = statistics.stdev(rr) if len(rr) > 1 else 0.0
    sharpe = statistics.mean(rr) / sd * math.sqrt(365.25) if sd else 0.0
    peak = curve[0]
    mdd = 0.0
    for x in curve:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1.0)
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
        raise RuntimeError("empty requested segment")
    first = selected[0][0]
    base = equity[first - 1] if first > 0 else 1.0
    curve = [1.0] + [e / base for _, _, e in selected]
    out = metrics(curve)
    out["start"] = selected[0][1]
    out["end"] = selected[-1][1]
    return out

def half_metrics(dates, equity):
    indices = [i for i, d in enumerate(dates) if d >= OOS_START]
    mid = len(indices) // 2
    def part(xs):
        first = xs[0]
        base = equity[first - 1] if first > 0 else 1.0
        curve = [1.0] + [equity[i] / base for i in xs]
        out = metrics(curve)
        out["start"] = dates[xs[0]]
        out["end"] = dates[xs[-1]]
        return out
    return {"first_half": part(indices[:mid]), "second_half": part(indices[mid:])}

def simulate(dates, px, funding, signals, cost_mult=1.0):
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
            eq *= 1.0 + pos * (px[d] / px[pd] - 1.0)
        if d in signals:
            target = signals[d]
            delta = abs(target - pos)
            eq *= max(0.0, 1.0 - (FEE + SLIP) * cost_mult * delta)
            turnover += delta / 2.0
            pos = target
        curve.append(eq)
    eq *= max(0.0, 1.0 - (FEE + SLIP) * cost_mult * abs(pos))
    curve[-1] = eq
    return {
        "metrics": metrics(curve),
        "oos": segment_metrics(dates, curve, OOS_START),
        "oos_halves": half_metrics(dates, curve),
        "turnover": turnover,
        "funding_pnl_sum": funding_sum,
        "equity": curve,
    }

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    dates, px, funding = load_price_and_funding()
    endpoints = {
        "HARMONY-FIN-0028": "https://api.blockchain.info/charts/n-unique-addresses?timespan=all&format=json&sampled=false",
        "HARMONY-FIN-0029": "https://api.blockchain.info/charts/hash-rate?timespan=all&format=json&sampled=false",
    }
    external = {}
    for name, url in endpoints.items():
        raw, series = fetch_chart(url)
        external[name] = {"url": url, "raw_sha256": sha256_bytes(raw), "rows": len(series), "series": series}

    results = {}
    intersections = {}
    for name, item in external.items():
        common = sorted(d for d in item["series"] if d in px and d <= END)
        if not common or common[-1] != END:
            raise RuntimeError(f"{name} missing end-date intersection")
        indicator = {d: item["series"][d] for d in common}
        smoothing = 7 if name == "HARMONY-FIN-0029" else 1
        signals = build_signal_map(common, indicator, smooth_window=smoothing, recent_window=30, rebalance_every=7)
        stress = {f"{m:.1f}x": simulate(dates, px, funding, signals, m) for m in (1.0, 1.5, 2.0)}
        base = stress["1.0x"]
        results[name] = {
            "base": {k: v for k, v in base.items() if k != "equity"},
            "cost_stress": {k: {"metrics": v["metrics"], "oos": v["oos"]} for k, v in stress.items()},
            "signal_count": len(signals),
        }
        intersections[name] = {"indicator_start": common[0], "indicator_end": common[-1], "intersection_rows": len(common)}

    bh = simulate(dates, px, funding, {dates[0]: 1.0}, 1.0)
    results["BTCUSDT_buy_and_hold"] = {"metrics": bh["metrics"], "oos": bh["oos"], "oos_halves": bh["oos_halves"]}

    manifest = {
        "batch_id": "HARMONY-DEEP-DISCOVERY-BATCH-005",
        "futures_cache_key": "harmony-binance-um-deep-history-2019-2025-10-v1-36777989764",
        "futures_panel": {"start": dates[0], "end": dates[-1], "rows": len(dates)},
        "external_sources": {
            name: {"url": v["url"], "raw_sha256": v["raw_sha256"], "rows": v["rows"]}
            for name, v in external.items()
        },
    }
    manifest_raw = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    manifest_sha = sha256_bytes(manifest_raw)
    (OUT / "input-manifest.json").write_bytes(manifest_raw)

    payload = {
        "batch_id": "HARMONY-DEEP-DISCOVERY-BATCH-005",
        "input_manifest_sha256": manifest_sha,
        "candidates": results,
        "data_intersection": intersections,
        "integrity": {
            "holdout_access": False,
            "parameter_search": False,
            "direction_search": False,
            "universe_search": False,
            "candidate_mutation": False,
        },
    }
    raw = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()
    result_sha = sha256_bytes(raw)
    (OUT / "HARMONY-DEEP-DISCOVERY-BATCH-005-RESULT.json").write_bytes(raw)
    (OUT / "HARMONY-DEEP-DISCOVERY-BATCH-005-SUMMARY.json").write_text(
        json.dumps(
            {
                "batch_id": payload["batch_id"],
                "result_sha256": result_sha,
                "candidates": {k: v.get("base", v) for k, v in results.items()},
            },
            sort_keys=True,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"batch_id": payload["batch_id"], "result_sha256": result_sha}, indent=2))

if __name__ == "__main__":
    main()
