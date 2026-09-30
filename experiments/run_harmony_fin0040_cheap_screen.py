import csv
import hashlib
import json
import math
import statistics
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SYMBOLS = ["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
FUT_ROOT = Path("data/cache/binance/futures_um/deep_history_2019")
OUT = Path("artifacts/HARMONY-CHEAP-SCREEN-FIN-0040-001")
END = "2024-05-21"
FEE = 0.0006
SLIP = 0.0005
MIN_REBALANCES = 20
MIN_CUMULATIVE_RETURN = 0.0
MIN_SHARPE = 0.0

def parse_zip_rows(path):
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise RuntimeError(f"unexpected archive members: {path}")
        return list(csv.reader(z.open(names[0]).read().decode("utf-8").splitlines()))

def skew_unbiased(values):
    n = len(values)
    if n < 3:
        raise ValueError("need at least 3 observations")
    mean = sum(values) / n
    m2 = sum((x - mean) ** 2 for x in values) / n
    m3 = sum((x - mean) ** 3 for x in values) / n
    if m2 <= 0:
        return 0.0
    return math.sqrt(n * (n - 1)) / (n - 2) * m3 / (m2 ** 1.5)

def load_panel():
    px = {s:{} for s in SYMBOLS}
    funding = {s:{} for s in SYMBOLS}
    for s in SYMBOLS:
        for path in sorted((FUT_ROOT / "klines" / s / "1d").glob(f"{s}-1d-*.zip")):
            for row in parse_zip_rows(path):
                if row and row[0].isdigit():
                    d = datetime.fromtimestamp(int(row[0]) / 1000.0, timezone.utc).date().isoformat()
                    if d <= END:
                        px[s][d] = float(row[4])
        fp = FUT_ROOT / "funding_gateway" / f"{s}-2019-2025-10.json"
        for row in json.loads(fp.read_text()):
            d = datetime.fromtimestamp(int(row["fundingTime"]) / 1000.0, timezone.utc).date().isoformat()
            if d <= END:
                funding[s].setdefault(d, []).append(float(row["fundingRate"]))
    dates = sorted(set.intersection(*(set(px[s]) for s in SYMBOLS)))
    if dates[0] != "2020-07-10" or dates[-1] != END:
        raise RuntimeError(f"unexpected discovery panel {dates[:1]}..{dates[-1:]} n={len(dates)}")
    return dates, px, funding

def month_key(d):
    return d[:7]

def month_ends(dates):
    out = {}
    for d in dates:
        out[month_key(d)] = d
    return out

def build_targets(dates, px):
    ends = month_ends(dates)
    months = sorted(ends)
    targets = {}
    for idx, m in enumerate(months):
        if idx == 0:
            continue
        formation_date = ends[months[idx - 1]]
        fi = dates.index(formation_date)
        if fi < 63:
            continue
        scores = []
        for s in SYMBOLS:
            rs = [px[s][dates[j]] / px[s][dates[j - 1]] - 1.0 for j in range(fi - 62, fi + 1)]
            if len(rs) != 63:
                raise RuntimeError("63-day formation window mismatch")
            scores.append((skew_unbiased(rs), s))
        scores.sort(key=lambda z: (z[0], z[1]))
        w = {s: 0.0 for s in SYMBOLS}
        for _, s in scores[:3]:
            w[s] = 1.0 / 6.0
        for _, s in scores[-3:]:
            w[s] = -1.0 / 6.0
        targets[m] = w
    return targets

def simulate(dates, px, funding, targets):
    eq = 1.0
    prev = {s: 0.0 for s in SYMBOLS}
    curve = []
    rebalance_count = 0
    turnover = 0.0
    funding_pnl_sum = 0.0
    last_month = None
    for i, d in enumerate(dates):
        for s in SYMBOLS:
            for rate in funding[s].get(d, []):
                pnl = -prev[s] * rate
                eq *= 1.0 + pnl
                funding_pnl_sum += pnl
        if i > 0:
            pd = dates[i - 1]
            eq *= 1.0 + sum(prev[s] * (px[s][d] / px[s][pd] - 1.0) for s in SYMBOLS)
        m = month_key(d)
        if m != last_month:
            target = targets.get(m)
            if target is not None:
                delta = sum(abs(target[s] - prev[s]) for s in SYMBOLS)
                eq *= max(0.0, 1.0 - (FEE + SLIP) * delta)
                turnover += delta / 2.0
                rebalance_count += 1
                prev = target.copy()
            last_month = m
        curve.append(eq)
    if not curve:
        raise RuntimeError("empty equity curve")
    liq = sum(abs(v) for v in prev.values())
    eq *= max(0.0, 1.0 - (FEE + SLIP) * liq)
    curve[-1] = eq
    return curve, rebalance_count, turnover, funding_pnl_sum

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

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    dates, px, funding = load_panel()
    targets = build_targets(dates, px)
    curve, rebalance_count, turnover, funding_pnl_sum = simulate(dates, px, funding, targets)
    m = metrics(curve)
    gates = {
        "minimum_rebalances": rebalance_count >= MIN_REBALANCES,
        "minimum_cumulative_return_after_costs": m["cumulative_return"] > MIN_CUMULATIVE_RETURN,
        "minimum_sharpe_after_costs": m["sharpe"] > MIN_SHARPE,
    }
    manifest = {
        "screen_id": "HARMONY-CHEAP-SCREEN-FIN-0040-001",
        "candidate": "HARMONY-FIN-0040",
        "futures_cache_key": "harmony-binance-um-deep-history-2019-2025-10-v1-36777989764",
        "panel": {"start": dates[0], "end": dates[-1], "rows": len(dates)},
        "discovery_end": END,
        "formation_window_days": 63,
        "feature": "adjusted Fisher-Pearson sample skewness",
        "portfolio": "long 3 lowest skewness; short 3 highest; equal gross weight",
        "costs": {"fee_rate": FEE, "slippage_rate": SLIP, "funding": "native"},
    }
    mraw = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    (OUT / "input-manifest.json").write_bytes(mraw)
    result = {
        "screen_id": manifest["screen_id"],
        "candidate": manifest["candidate"],
        "metrics_after_costs": m,
        "rebalance_count": rebalance_count,
        "turnover": turnover,
        "funding_pnl_sum": funding_pnl_sum,
        "gates": gates,
        "passed": all(gates.values()),
        "integrity": {
            "holdout_access": False,
            "parameter_search": False,
            "universe_search": False,
            "direction_search": False,
            "candidate_mutation": False,
        },
    }
    raw = (json.dumps(result, sort_keys=True, indent=2) + "\n").encode()
    result_sha = hashlib.sha256(raw).hexdigest()
    (OUT / "HARMONY-CHEAP-SCREEN-FIN-0040-001-RESULT.json").write_bytes(raw)
    (OUT / "HARMONY-CHEAP-SCREEN-FIN-0040-001-SUMMARY.json").write_text(
        json.dumps({"screen_id": result["screen_id"], "passed": result["passed"], "result_sha256": result_sha}, sort_keys=True, indent=2) + "\n"
    )
    print(json.dumps({"screen_id": result["screen_id"], "passed": result["passed"], "result_sha256": result_sha}, indent=2))

if __name__ == "__main__":
    main()
