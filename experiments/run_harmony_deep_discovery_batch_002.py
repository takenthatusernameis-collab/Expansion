import csv
import hashlib
import io
import json
import math
import statistics
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

SYMBOLS = ["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
FUT_ROOT = Path("data/cache/binance/futures_um/deep_history_2019")
SPOT_ROOT = Path("data/cache/binance/spot/deep_history_2019")
COT_ROOT = Path("data/cache/cftc/cot_legacy")
OUT = Path("artifacts/HARMONY-DEEP-DISCOVERY-BATCH-002")
START = date(2019, 1, 1)
END = date(2025, 10, 31)
OOS_START = "2024-05-22"
FEE = 0.0006
SLIP = 0.0005

def fetch(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": "Harmony/DEEP-DISCOVERY-BATCH-002"})
    with urlopen(req, timeout=120) as r:
        return r.read()

def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()

def months(a: date, b: date):
    y, m = a.year, a.month
    out = []
    while (y, m) <= (b.year, b.month):
        out.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out

def parse_zip_rows(path: Path):
    with zipfile.ZipFile(path) as z:
        if z.testzip() is not None:
            raise ValueError(f"archive CRC failure: {path}")
        names = [n for n in z.namelist() if n.lower().endswith((".csv", ".txt"))]
        if not names:
            raise ValueError(f"no CSV/TXT in {path}")
        raw = z.read(names[0]).decode("utf-8", "replace")
        return list(csv.reader(io.StringIO(raw)))

def materialize_binance_spot_month(symbol: str, year: int, month: int) -> Path:
    ym = f"{year:04d}-{month:02d}"
    filename = f"{symbol}-1d-{ym}.zip"
    path = SPOT_ROOT / "klines" / symbol / "1d" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://data.binance.vision/data/spot/monthly/klines/{symbol}/1d/{filename}"
    if path.exists():
        raw = path.read_bytes()
        checksum = fetch(url + ".CHECKSUM").decode("utf-8", "replace").strip().split()[0].lower()
        if sha256_bytes(raw) != checksum:
            path.unlink()
        else:
            return path
    raw = fetch(url)
    checksum = fetch(url + ".CHECKSUM").decode("utf-8", "replace").strip().split()[0].lower()
    if sha256_bytes(raw) != checksum:
        raise ValueError(f"spot checksum mismatch: {filename}")
    path.write_bytes(raw)
    return path

def load_daily_closes(root: Path, symbol: str, source_kind: str):
    closes = {}
    for year, month in months(START, END):
        if source_kind == "futures":
            path = root / "klines" / symbol / "1d" / f"{symbol}-1d-{year:04d}-{month:02d}.zip"
        else:
            path = materialize_binance_spot_month(symbol, year, month)
        if not path.is_file():
            continue
        for row in parse_zip_rows(path):
            if row and row[0].isdigit() and len(row) >= 5:
                d = datetime.fromtimestamp(int(row[0]) / 1000.0, timezone.utc).date().isoformat()
                closes[d] = float(row[4])
    return closes

def load_funding(symbol: str):
    path = FUT_ROOT / "funding_gateway" / f"{symbol}-2019-2025-10.json"
    rows = json.loads(path.read_text())
    out = {}
    for row in rows:
        d = datetime.fromtimestamp(int(row["fundingTime"]) / 1000.0, timezone.utc).date().isoformat()
        out.setdefault(d, []).append(float(row["fundingRate"]))
    return out

def cot_normalize(name: str) -> str:
    return "".join(ch.lower() for ch in name if ch.isalnum())

def load_cot_rows():
    records = []
    COT_ROOT.mkdir(parents=True, exist_ok=True)
    for year in range(2017, 2026):
        path = COT_ROOT / f"deacot{year}.zip"
        url = f"https://www.cftc.gov/files/dea/history/deacot{year}.zip"
        if path.exists():
            raw = path.read_bytes()
        else:
            raw = fetch(url)
            path.write_bytes(raw)

        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            txt_names = [n for n in z.namelist() if n.lower().endswith((".txt", ".csv"))]
            selected = None
            for name in txt_names:
                header_raw = z.read(name).decode("utf-8", "replace").splitlines()
                if header_raw and "Market and Exchange Names" in header_raw[0]:
                    selected = z.read(name).decode("utf-8", "replace")
                    break
            if selected is None:
                raise ValueError(f"no legacy COT file found in {path}")

        reader = csv.DictReader(io.StringIO(selected))
        headers = reader.fieldnames or []
        norm = {cot_normalize(h): h for h in headers}

        market_h = norm.get(cot_normalize("Market and Exchange Names"))
        date_h = norm.get(cot_normalize("As of Date in Form YYYY-MM-DD")) or norm.get(cot_normalize("As of Date in Form YYYY-MM-DD"))
        oi_h = norm.get(cot_normalize("Open Interest (All)"))
        long_h = norm.get(cot_normalize("Noncommercial Positions-Long (All)"))
        short_h = norm.get(cot_normalize("Noncommercial Positions-Short (All)"))

        if not all([market_h, date_h, oi_h, long_h, short_h]):
            raise ValueError(f"COT header mismatch in {path}: {headers[:20]}")

        for row in reader:
            market = (row.get(market_h) or "").strip().upper()
            if market != "BITCOIN - CHICAGO MERCANTILE EXCHANGE":
                continue
            report_date = (row.get(date_h) or "").strip()
            if not report_date:
                continue
            oi = float((row.get(oi_h) or "0").replace(",", ""))
            nlong = float((row.get(long_h) or "0").replace(",", ""))
            nshort = float((row.get(short_h) or "0").replace(",", ""))
            if oi <= 0:
                continue
            records.append({
                "report_date": report_date,
                "open_interest": oi,
                "noncommercial_long": nlong,
                "noncommercial_short": nshort,
                "net_short_share": (nshort - nlong) / oi,
            })
    records.sort(key=lambda x: x["report_date"])
    dedup = {}
    for row in records:
        dedup[row["report_date"]] = row
    return [dedup[d] for d in sorted(dedup)]

def metrics(curve):
    if not curve:
        raise ValueError("empty equity curve")
    curve = list(curve)
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
    selected = [(d, e) for d, e in zip(dates, equity) if d >= start_date]
    if not selected:
        return None
    base = selected[0][1]
    curve = [e / base for _, e in selected]
    return metrics(curve)

def halves_metrics(dates, equity, start_date):
    selected = [(d, e) for d, e in zip(dates, equity) if d >= start_date]
    if len(selected) < 4:
        return None
    mid = len(selected) // 2
    first = selected[:mid]
    second = selected[mid:]
    def run(part):
        base = part[0][1]
        return metrics([e / base for _, e in part])
    return {"first_half": run(first), "second_half": run(second)}

def simulate_panel(dates, close, funding, weight_fn, rebalance_fn, cost_mult=1.0):
    eq = 1.0
    prev = {s: 0.0 for s in SYMBOLS}
    curve = []
    turnover = 0.0
    funding_sum = 0.0

    for i, d in enumerate(dates):
        for s in SYMBOLS:
            for rate in funding.get(s, {}).get(d, []):
                pnl = -prev[s] * rate
                eq *= 1.0 + pnl
                funding_sum += pnl

        if i > 0:
            pd = dates[i - 1]
            eq *= 1.0 + sum(
                prev[s] * (close[s][d] / close[s][pd] - 1.0)
                for s in SYMBOLS
            )

        if rebalance_fn(i, d):
            target = weight_fn(i, d)
            if target is not None:
                delta = sum(abs(target[s] - prev[s]) for s in SYMBOLS)
                turnover += delta / 2.0
                eq *= max(0.0, 1.0 - (FEE + SLIP) * cost_mult * delta)
                prev = target
        curve.append(eq)

    liquidation = sum(abs(v) for v in prev.values())
    eq *= max(0.0, 1.0 - (FEE + SLIP) * cost_mult * liquidation)
    curve[-1] = eq

    return {
        "metrics": metrics(curve),
        "oos": segment_metrics(dates, curve, OOS_START),
        "oos_halves": halves_metrics(dates, curve, OOS_START),
        "turnover": turnover,
        "funding_pnl_sum": funding_sum,
        "equity": curve,
    }

def basis_weights(dates, futures, spot, i):
    if i < 1:
        return None
    signal_date = dates[i - 1]
    scores = []
    for s in SYMBOLS:
        basis = futures[s][signal_date] / spot[s][signal_date] - 1.0
        scores.append((basis, s))
    scores.sort(key=lambda x: (-x[0], x[1]))
    w = {s: 0.0 for s in SYMBOLS}
    for _, s in scores[:3]:
        w[s] = 1.0 / 6.0
    for _, s in scores[-3:]:
        w[s] = -1.0 / 6.0
    return w

def build_basis_results(futures, spot, funding):
    common = sorted(set.intersection(*(set(futures[s]) & set(spot[s]) for s in SYMBOLS)))
    common = [d for d in common if d <= END.isoformat()]
    if len(common) < 500:
        raise RuntimeError(f"basis common history too short: {len(common)} observations")

    def rebalance(i, d):
        return i >= 1 and (i - 1) % 7 == 0

    def weights(i, d):
        return basis_weights(common, futures, spot, i)

    results = {}
    for m in (1.0, 1.5, 2.0):
        results[f"{m:.1f}x"] = simulate_panel(common, futures, funding, weights, rebalance, m)

    base = results["1.0x"]
    return {
        "full_trace": {"start": common[0], "end": common[-1], "observations": len(common)},
        "base": {k: v for k, v in base.items() if k != "equity"},
        "cost_stress": {k: {
            "metrics": v["metrics"],
            "oos": v["oos"],
            "oos_halves": v["oos_halves"],
        } for k, v in results.items()},
    }

def cftc_position_signal(report_rows):
    if len(report_rows) < 2:
        raise RuntimeError("insufficient CFTC reports")
    signals = []
    previous = None
    for row in report_rows:
        if previous is not None:
            change = row["net_short_share"] - previous["net_short_share"]
            report_date = date.fromisoformat(row["report_date"])
            activation = report_date + timedelta(days=(5 - report_date.weekday()) % 7)
            # If report date is Tuesday, activation is the following Saturday.
            if activation <= report_date:
                activation += timedelta(days=7)
            signals.append((activation.isoformat(), change))
        previous = row
    return signals

def simulate_btc_cot(btc_close, funding, signals, cost_mult=1.0):
    dates = sorted(d for d in btc_close if d <= END.isoformat())
    signal_map = dict(signals)
    eq = 1.0
    pos = 0.0
    curve = []
    turnover = 0.0
    funding_sum = 0.0

    for i, d in enumerate(dates):
        if funding.get(d):
            for rate in funding[d]:
                pnl = -pos * rate
                eq *= 1.0 + pnl
                funding_sum += pnl

        if i > 0:
            pd = dates[i - 1]
            eq *= 1.0 + pos * (btc_close[d] / btc_close[pd] - 1.0)

        if d in signal_map:
            signal = signal_map[d]
            target = 1.0 if signal > 0 else -1.0 if signal < 0 else 0.0
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
        "trace_dates": dates,
    }

def main():
    OUT.mkdir(parents=True, exist_ok=True)

    futures = {s: load_daily_closes(FUT_ROOT, s, "futures") for s in SYMBOLS}
    spot = {s: load_daily_closes(SPOT_ROOT, s, "spot") for s in SYMBOLS}
    funding = {s: load_funding(s) for s in SYMBOLS}

    # FIN-0017 deep basis backtest.
    basis_result = build_basis_results(futures, spot, funding)

    # CFTC positioning deep backtest.
    cot_rows = load_cot_rows()
    btc_funding = funding["BTCUSDT"]
    cot_signals = cftc_position_signal(cot_rows)
    cot_results = {}
    for m in (1.0, 1.5, 2.0):
        cot_results[f"{m:.1f}x"] = simulate_btc_cot(
            futures["BTCUSDT"], btc_funding, cot_signals, m
        )

    # Benchmarks for the OOS interpretation.
    common = sorted(set.intersection(*(set(futures[s]) & set(spot[s]) for s in SYMBOLS)))
    common = [d for d in common if d <= END.isoformat()]
    equal = simulate_panel(
        common,
        futures,
        funding,
        lambda i, d: {s: 1.0 / len(SYMBOLS) for s in SYMBOLS},
        lambda i, d: i % 7 == 0,
        1.0,
    )
    btc = simulate_btc_cot(
        futures["BTCUSDT"],
        funding["BTCUSDT"],
        [(common[0], 0.0)],
        1.0,
    )
    # Replace the dummy positioning signal with a true BTC buy-and-hold curve.
    btc_dates = sorted(d for d in futures["BTCUSDT"] if d <= END.isoformat())
    btc_curve = [futures["BTCUSDT"][d] / futures["BTCUSDT"][btc_dates[0]] for d in btc_dates]
    btc_bh = {
        "metrics": metrics(btc_curve),
        "oos": segment_metrics(btc_dates, btc_curve, OOS_START),
        "oos_halves": halves_metrics(btc_dates, btc_curve, OOS_START),
    }

    spot_files = []
    for path in sorted(SPOT_ROOT.rglob("*.zip")):
        raw = path.read_bytes()
        spot_files.append({
            "path": str(path),
            "bytes": len(raw),
            "sha256": sha256_bytes(raw),
        })
    cot_files = []
    for path in sorted(COT_ROOT.glob("deacot*.zip")):
        raw = path.read_bytes()
        cot_files.append({
            "path": str(path),
            "bytes": len(raw),
            "sha256": sha256_bytes(raw),
        })

    input_manifest = {
        "batch_id": "HARMONY-DEEP-DISCOVERY-BATCH-002",
        "futures_cache_key": "harmony-binance-um-deep-history-2019-2025-10-v1-36777989764",
        "futures_common_history": {
            s: {
                "start": min(futures[s]) if futures[s] else None,
                "end": max(futures[s]) if futures[s] else None,
                "rows": len(futures[s]),
            }
            for s in SYMBOLS
        },
        "spot_files": spot_files,
        "cftc_files": cot_files,
        "cftc_source": "https://www.cftc.gov/files/dea/history/deacotYYYY.zip",
        "selection_end": END.isoformat(),
        "oos_start": OOS_START,
        "github_sha": __import__("os").environ.get("GITHUB_SHA"),
    }
    manifest_bytes = json.dumps(input_manifest, sort_keys=True, indent=2).encode() + b"\n"
    manifest_sha = sha256_bytes(manifest_bytes)
    (OUT / "input-manifest.json").write_bytes(manifest_bytes)

    payload = {
        "batch_id": "HARMONY-DEEP-DISCOVERY-BATCH-002",
        "input_manifest_sha256": manifest_sha,
        "candidates": {
            "HARMONY-FIN-0017": basis_result,
            "HARMONY-FIN-0018": {
                "full_trace": {
                    "cftc_reports": len(cot_rows),
                    "first_report": cot_rows[0]["report_date"] if cot_rows else None,
                    "last_report": cot_rows[-1]["report_date"] if cot_rows else None,
                    "btc_price_start": min(futures["BTCUSDT"]),
                    "btc_price_end": max(futures["BTCUSDT"]),
                },
                "base": {
                    "metrics": cot_results["1.0x"]["metrics"],
                    "oos": cot_results["1.0x"]["oos"],
                    "oos_halves": cot_results["1.0x"]["oos_halves"],
                    "turnover": cot_results["1.0x"]["turnover"],
                    "funding_pnl_sum": cot_results["1.0x"]["funding_pnl_sum"],
                },
                "cost_stress": {
                    k: {"metrics": v["metrics"], "oos": v["oos"], "oos_halves": v["oos_halves"]}
                    for k, v in cot_results.items()
                },
            },
        },
        "benchmarks": {
            "same_universe_equal_weight_long_only": {
                "metrics": equal["metrics"],
                "oos": equal["oos"],
                "oos_halves": equal["oos_halves"],
            },
            "BTCUSDT_buy_and_hold": btc_bh,
        },
        "integrity": {
            "holdout_access": False,
            "parameter_search": False,
            "universe_search": False,
            "candidate_mutation": False,
            "cot_release_lag_conservative": True,
        },
    }

    raw = json.dumps(payload, sort_keys=True, indent=2).encode() + b"\n"
    result_sha = sha256_bytes(raw)
    (OUT / "HARMONY-DEEP-DISCOVERY-BATCH-002-RESULT.json").write_bytes(raw)
    (OUT / "HARMONY-DEEP-DISCOVERY-BATCH-002-SUMMARY.json").write_text(
        json.dumps(
            {
                "batch_id": payload["batch_id"],
                "result_sha256": result_sha,
                "candidates": {
                    name: data["base"]
                    for name, data in payload["candidates"].items()
                },
                "holdout_access": False,
            },
            sort_keys=True,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"batch_id": payload["batch_id"], "result_sha256": result_sha}, indent=2))

if __name__ == "__main__":
    main()
