import csv, hashlib, json, math, statistics, zipfile
from datetime import datetime, timezone
from pathlib import Path

SYMBOLS = ["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
ROOT = Path("data/cache/binance/futures_um/deep_history_2019")
OUT = Path("artifacts/HARMONY-FIN-0056-PROMOTED-VALIDATION-001")
END = "2025-10-31"
OOS_START = "2024-05-22"
FEE = 0.0006
SLIP = 0.0005

EXPECTED = {
    "cumulative_return": 0.28467078513220323,
    "sharpe": 1.035538238338156,
    "second_half_sharpe": 0.5135256165483285,
}

def parse_zip(path):
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise RuntimeError(f"unexpected archive shape: {path}")
        return list(csv.reader(z.open(names[0]).read().decode("utf-8").splitlines()))

def load_panel():
    px = {s:{} for s in SYMBOLS}
    funding = {s:{} for s in SYMBOLS}
    for s in SYMBOLS:
        for path in sorted((ROOT / "klines" / s / "1d").glob(f"{s}-1d-*.zip")):
            for row in parse_zip(path):
                if row and row[0].isdigit():
                    d = datetime.fromtimestamp(int(row[0]) / 1000.0, timezone.utc).date().isoformat()
                    if d <= END:
                        px[s][d] = float(row[4])
        fp = ROOT / "funding_gateway" / f"{s}-2019-2025-10.json"
        for row in json.loads(fp.read_text()):
            d = datetime.fromtimestamp(int(row["fundingTime"]) / 1000.0, timezone.utc).date().isoformat()
            if d <= END:
                funding[s].setdefault(d, []).append(float(row["fundingRate"]))
    dates = sorted(set.intersection(*(set(px[s]) for s in SYMBOLS)))
    if (dates[0], dates[-1], len(dates)) != ("2020-07-10", END, 1935):
        raise RuntimeError(f"unexpected common panel: {dates[:1]}..{dates[-1:]} n={len(dates)}")
    return dates, px, funding

def returns(dates, px):
    return {
        s: {
            d: 0.0 if i == 0 else px[s][d] / px[s][dates[i - 1]] - 1.0
            for i, d in enumerate(dates)
        }
        for s in SYMBOLS
    }

def month_key(d):
    return d[:7]

def population_kurtosis(values):
    n = len(values)
    mean = sum(values) / n
    m2 = sum((x - mean) ** 2 for x in values) / n
    if m2 <= 0:
        return 0.0
    m4 = sum((x - mean) ** 4 for x in values) / n
    return m4 / (m2 * m2)

def build_targets(dates, r):
    targets = {}
    for i, d in enumerate(dates):
        if i < 60 or (i > 0 and month_key(dates[i - 1]) == month_key(d)):
            continue
        scores = [
            (
                population_kurtosis([r[s][dates[j]] for j in range(i - 60, i)]),
                s,
            )
            for s in SYMBOLS
        ]
        ordered = sorted(scores, key=lambda x: (x[0], x[1]))
        target = {s: 0.0 for s in SYMBOLS}
        for _, s in ordered[:3]:
            target[s] = -1.0 / 6.0
        for _, s in ordered[-3:]:
            target[s] = 1.0 / 6.0
        if abs(sum(abs(target[s]) for s in SYMBOLS) - 1.0) > 1e-12:
            raise RuntimeError("gross exposure invariant failed")
        targets[d] = target
    return targets

def simulate(dates, px, funding, targets, cost_mult=1.0, apply_funding=True):
    eq = 1.0
    prev = {s: 0.0 for s in SYMBOLS}
    curve = []
    funding_sum = 0.0
    turnover = 0.0
    rebalances = 0
    for i, d in enumerate(dates):
        if apply_funding:
            for s in SYMBOLS:
                for rate in funding[s].get(d, []):
                    pnl = -prev[s] * rate
                    eq *= 1.0 + pnl
                    funding_sum += pnl
        if i > 0:
            pd = dates[i - 1]
            eq *= 1.0 + sum(
                prev[s] * (px[s][d] / px[s][pd] - 1.0)
                for s in SYMBOLS
            )
        target = targets.get(d)
        if target is not None:
            delta = sum(abs(target[s] - prev[s]) for s in SYMBOLS)
            eq *= max(0.0, 1.0 - (FEE + SLIP) * cost_mult * delta)
            turnover += delta / 2.0
            rebalances += 1
            prev = target.copy()
        curve.append(eq)
    liquidation = sum(abs(v) for v in prev.values())
    eq *= max(0.0, 1.0 - (FEE + SLIP) * cost_mult * liquidation)
    curve[-1] = eq
    return curve, turnover, rebalances, funding_sum

def metrics(curve):
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

def slice_oos(dates, curve, start=OOS_START, end=None):
    idx = [i for i, d in enumerate(dates) if d >= start and (end is None or d <= end)]
    base = curve[idx[0] - 1] if idx[0] > 0 else 1.0
    return metrics([1.0] + [curve[i] / base for i in idx])

def oos_halves(dates, curve):
    idx = [i for i, d in enumerate(dates) if d >= OOS_START]
    mid = len(idx) // 2
    def part(xs):
        base = curve[xs[0] - 1] if xs[0] > 0 else 1.0
        m = metrics([1.0] + [curve[i] / base for i in xs])
        m.update(start=dates[xs[0]], end=dates[xs[-1]])
        return m
    return {"first_half": part(idx[:mid]), "second_half": part(idx[mid:])}

def normalized_curve(dates, curve):
    idx = [i for i, d in enumerate(dates) if d >= OOS_START]
    base = curve[idx[0] - 1] if idx[0] > 0 else 1.0
    return [1.0] + [curve[i] / base for i in idx]

def compare_metrics(a, b):
    return {k: a[k] - b[k] for k in ("cumulative_return", "sharpe")}

def run_once(dates, px, funding, targets, cost_mult=1.0, apply_funding=True):
    curve, turnover, rebalances, funding_sum = simulate(
        dates, px, funding, targets, cost_mult=cost_mult, apply_funding=apply_funding
    )
    return {
        "full": metrics(curve),
        "oos": slice_oos(dates, curve),
        "oos_halves": oos_halves(dates, curve),
        "turnover": turnover,
        "rebalance_count": rebalances,
        "funding_pnl_sum": funding_sum,
    }

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    dates, px, funding = load_panel()
    r = returns(dates, px)
    targets = build_targets(dates, r)

    baseline_a = run_once(dates, px, funding, targets, 1.0, True)
    baseline_b = run_once(dates, px, funding, targets, 1.0, True)
    reproduction = {
        "first_run": baseline_a["oos"],
        "second_run": baseline_b["oos"],
        "delta": compare_metrics(baseline_a["oos"], baseline_b["oos"]),
        "matches_batch010": (
            abs(baseline_a["oos"]["cumulative_return"] - EXPECTED["cumulative_return"]) <= 1e-12
            and abs(baseline_a["oos"]["sharpe"] - EXPECTED["sharpe"]) <= 1e-12
        ),
    }
    if not reproduction["matches_batch010"]:
        raise RuntimeError("FIN-0056 baseline reproduction failed against Batch-010 accepted result")
    if abs(reproduction["delta"]["cumulative_return"]) > 1e-12 or abs(reproduction["delta"]["sharpe"]) > 1e-12:
        raise RuntimeError("FIN-0056 repeated deterministic run mismatch")

    stress = {
        "1.0x_cost_funding_on": baseline_a,
        "2.0x_cost_funding_on": run_once(dates, px, funding, targets, 2.0, True),
        "3.0x_cost_funding_on": run_once(dates, px, funding, targets, 3.0, True),
        "1.0x_cost_funding_neutral": run_once(dates, px, funding, targets, 1.0, False),
    }
    halves = baseline_a["oos_halves"]
    diagnostics = {
        "baseline_reproduction_pass": reproduction["matches_batch010"],
        "three_x_cost_sharpe_gt_0_5": stress["3.0x_cost_funding_on"]["oos"]["sharpe"] > 0.5,
        "funding_neutral_sharpe_gt_0_5": stress["1.0x_cost_funding_neutral"]["oos"]["sharpe"] > 0.5,
        "both_oos_halves_positive_sharpe": (
            halves["first_half"]["sharpe"] > 0.0 and halves["second_half"]["sharpe"] > 0.0
        ),
    }
    final_status = "ROBUSTNESS_SUPPORTED" if all(diagnostics.values()) else "ROBUSTNESS_MIXED"

    manifest = {
        "validation_id": "HARMONY-FIN-0056-PROMOTED-VALIDATION-001",
        "candidate": "HARMONY-FIN-0056",
        "cache_key": "harmony-binance-um-deep-history-2019-2025-10-v1-36777989764",
        "panel": {"start": dates[0], "end": dates[-1], "rows": len(dates), "symbols": SYMBOLS},
        "oos_start": OOS_START,
        "signal": "monthly long highest / short lowest population kurtosis over prior 60 completed daily returns",
        "costs": {"fee_rate": FEE, "slippage_rate": SLIP},
        "no_parameter_search": True,
        "no_universe_search": True,
        "no_direction_search": True,
        "no_holdout_access": True,
    }
    mraw = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    (OUT / "input-manifest.json").write_bytes(mraw)

    payload = {
        "validation_id": "HARMONY-FIN-0056-PROMOTED-VALIDATION-001",
        "status": final_status,
        "input_manifest_sha256": hashlib.sha256(mraw).hexdigest(),
        "reproduction": reproduction,
        "stress": stress,
        "diagnostics": diagnostics,
        "integrity": {
            "fixed_definition": True,
            "parameter_search": False,
            "universe_search": False,
            "direction_search": False,
            "holdout_access": False,
            "candidate_mutation": False,
        },
    }
    raw = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()
    result_sha = hashlib.sha256(raw).hexdigest()
    (OUT / "HARMONY-FIN-0056-PROMOTED-VALIDATION-001-RESULT.json").write_bytes(raw)
    (OUT / "HARMONY-FIN-0056-PROMOTED-VALIDATION-001-SUMMARY.json").write_text(
        json.dumps(
            {
                "validation_id": payload["validation_id"],
                "status": final_status,
                "result_sha256": result_sha,
                "diagnostics": diagnostics,
                "baseline_oos_sharpe": baseline_a["oos"]["sharpe"],
                "three_x_cost_oos_sharpe": stress["3.0x_cost_funding_on"]["oos"]["sharpe"],
                "funding_neutral_oos_sharpe": stress["1.0x_cost_funding_neutral"]["oos"]["sharpe"],
            },
            sort_keys=True,
            indent=2,
        ) + "\n"
    )
    print(json.dumps({"status": final_status, "result_sha256": result_sha}, indent=2))

if __name__ == "__main__":
    main()
