import csv
import hashlib
import json
import math
import statistics
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SYMBOLS = ["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
ROOT = Path("data/cache/binance/futures_um/deep_history_2019")
OUT = Path("artifacts/HARMONY-FIN-0013")
BINDING_PATH = Path("research/HARMONY-FIN-0013-BINDING.json")
SPEC_PATH = Path("experiments/HARMONY-FIN-0013.yaml")
FEE = 0.0006
SLIP = 0.0005
OOS_START = "2024-05-22"
END = "2025-10-31"
EXPECTED_PANEL_SHA = "2b28b2975dd2c77fabb35d4641892b24855a79b55eedeba5b8fada71a97f7e2c"
EXPECTED_MANIFEST_SHA = "14a8872ed007efadf5237e6d931a21c230bfa34e0b80a6020120767abb095c41"
EXPECTED_CANDIDATE_DIGEST = "c9d8e3491ecb067e4f09d121b1acc50e5a7a9bfc8eaf2f1109550c3731004110"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_zip_csv(path: Path):
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise RuntimeError(f"unexpected archive shape: {path}")
        return list(csv.reader(z.read(names[0]).decode("utf-8").splitlines()))


def day_from_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).date().isoformat()


def load_prices():
    prices = {s: {} for s in SYMBOLS}
    for s in SYMBOLS:
        base = ROOT / "klines" / s / "1d"
        files = sorted(base.glob(f"{s}-1d-*.zip"))
        if not files:
            raise FileNotFoundError(base)
        for path in files:
            for row in parse_zip_csv(path):
                if row and row[0].isdigit():
                    prices[s][day_from_ms(int(row[0]))] = float(row[4])
    dates = sorted(set.intersection(*(set(prices[s]) for s in SYMBOLS)))
    dates = [d for d in dates if d <= END]
    if not dates:
        raise RuntimeError("empty common panel")
    return prices, dates


def load_funding():
    funding = {s: [] for s in SYMBOLS}
    for s in SYMBOLS:
        path = ROOT / "funding_gateway" / f"{s}-2019-2025-10.json"
        if not path.is_file():
            raise FileNotFoundError(path)
        rows = json.loads(path.read_text())
        for row in rows:
            funding[s].append((int(row["fundingTime"]), float(row["fundingRate"])))
        funding[s].sort()
    return funding


def normalized_panel_sha(prices, dates) -> str:
    buf = ["date," + ",".join(f"{s}_close" for s in SYMBOLS)]
    for d in dates:
        buf.append(d + "," + ",".join(f"{prices[s][d]:.17g}" for s in SYMBOLS))
    return sha256_bytes(("\n".join(buf) + "\n").encode())


def weights(prices, dates, i):
    if i < 21:
        return None
    d = dates[i]
    scores = {}
    for s in SYMBOLS:
        score = prices[s][dates[i - 1]] / prices[s][dates[i - 21]] - 1.0
        scores[s] = 1 if score > 0 else (-1 if score < 0 else 0)
    pos = [s for s in SYMBOLS if scores[s] > 0]
    neg = [s for s in SYMBOLS if scores[s] < 0]
    w = {s: 0.0 for s in SYMBOLS}
    if pos and neg:
        for s in pos:
            w[s] = 0.5 / len(pos)
        for s in neg:
            w[s] = -0.5 / len(neg)
    elif pos:
        for s in pos:
            w[s] = 1.0 / len(pos)
    elif neg:
        for s in neg:
            w[s] = -1.0 / len(neg)
    return w


def metrics(equity):
    start_idx = 0
    first = equity[0]
    rr = [equity[i] / equity[i - 1] - 1.0 for i in range(1, len(equity))]
    sd = statistics.stdev(rr) if len(rr) > 1 else 0.0
    sharpe = statistics.mean(rr) / sd * math.sqrt(365.25) if sd else 0.0
    peak = first
    mdd = 0.0
    for x in equity:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1.0)
    years = max((len(equity) - 1) / 365.25, 1e-12)
    cagr = equity[-1] ** (1.0 / years) - 1.0
    return {
        "final_equity": equity[-1],
        "cumulative_return": equity[-1] - 1.0,
        "cagr": cagr,
        "sharpe": sharpe,
        "max_drawdown": mdd,
        "observations": len(equity),
    }


def simulate(prices, dates, funding, mode="strategy", cost_mult=1.0):
    eq = 1.0
    prev = {s: 0.0 for s in SYMBOLS}
    curve = []
    turnover = 0.0
    funding_pnl = 0.0
    funding_events = 0
    first_rebalance = None

    # Position is determined at the close. Funding on a date therefore applies
    # before that date's close-time rebalance, using the position from the prior close.
    for i, d in enumerate(dates):
        for s in SYMBOLS:
            for ts, rate in funding[s]:
                if day_from_ms(ts) == d:
                    pnl = -prev[s] * rate
                    eq *= 1.0 + pnl
                    funding_pnl += pnl
                    funding_events += 1

        if i > 0:
            pd = dates[i - 1]
            eq *= 1.0 + sum(prev[s] * (prices[s][d] / prices[s][pd] - 1.0) for s in SYMBOLS)

        rebalance = i >= 21 and ((i - 21) % 7 == 0)
        if rebalance:
            if mode == "strategy":
                tgt = weights(prices, dates, i)
            elif mode == "equal":
                tgt = {s: 1.0 / len(SYMBOLS) for s in SYMBOLS}
            elif mode == "btc":
                tgt = {s: (1.0 if s == "BTCUSDT" else 0.0) for s in SYMBOLS}
            else:
                raise ValueError(mode)
            if first_rebalance is None:
                first_rebalance = d
        else:
            tgt = prev.copy()

        delta = sum(abs(tgt[s] - prev[s]) for s in SYMBOLS)
        turnover += delta / 2.0
        eq *= 1.0 - (FEE + SLIP) * cost_mult * delta
        prev = tgt
        curve.append(eq)

    liquidation = sum(abs(x) for x in prev.values())
    eq *= 1.0 - (FEE + SLIP) * cost_mult * liquidation
    curve[-1] = eq

    return {
        "metrics": metrics(curve),
        "one_way_turnover": turnover,
        "funding_pnl_sum": funding_pnl,
        "funding_events": funding_events,
        "first_rebalance": first_rebalance,
    }


def oos_slice(equity_by_date, start):
    return [v for d, v in equity_by_date if d >= start]


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    binding = json.loads(BINDING_PATH.read_text())
    if binding["experiment_id"] != "HARMONY-FIN-0013":
        raise RuntimeError("binding experiment mismatch")
    if binding["candidate_digest"] != EXPECTED_CANDIDATE_DIGEST:
        raise RuntimeError("candidate digest mismatch")
    if binding["manifest_sha256"] != EXPECTED_MANIFEST_SHA:
        raise RuntimeError("manifest binding mismatch")

    prices, dates = load_prices()
    funding = load_funding()

    if dates[0] != "2020-07-10" or dates[-1] != END or len(dates) != 1935:
        raise RuntimeError(f"unexpected common panel {dates[0]}..{dates[-1]} rows={len(dates)}")

    panel_sha = normalized_panel_sha(prices, dates)
    if panel_sha != EXPECTED_PANEL_SHA:
        raise RuntimeError(f"normalized panel SHA mismatch: {panel_sha}")

    for s in SYMBOLS:
        if not funding[s]:
            raise RuntimeError(f"missing funding history: {s}")

    base = simulate(prices, dates, funding, "strategy", 1.0)
    bench_equal = simulate(prices, dates, funding, "equal", 1.0)
    bench_btc = simulate(prices, dates, funding, "btc", 1.0)

    # Re-run at predefined cost stress levels; no parameter changes.
    stress = {f"{m:.1f}x": simulate(prices, dates, funding, "strategy", m)["metrics"] for m in (1.0, 1.5, 2.0)}

    result = {
        "experiment_id": "HARMONY-FIN-0013",
        "candidate_id": "time_series_momentum_20d_weekly_v1",
        "candidate_digest": EXPECTED_CANDIDATE_DIGEST,
        "binding_id": binding["binding_id"],
        "acquisition_workflow_run_id": binding["workflow_run_id"],
        "acquisition_artifact_id": binding["artifact_id"],
        "acquisition_artifact_sha256": binding["artifact_sha256"],
        "input_manifest_sha256": binding["manifest_sha256"],
        "normalized_panel_sha256": panel_sha,
        "full_trace": {"start": dates[0], "end": dates[-1], "observations": len(dates)},
        "oos_boundary": OOS_START,
        "strategy": base,
        "benchmarks": {
            "same_universe_equal_weight_long_only": bench_equal,
            "BTCUSDT_buy_and_hold": bench_btc,
        },
        "predefined_cost_stress": stress,
        "integrity": {
            "holdout_start": "2025-11-01",
            "holdout_access": False,
            "parameter_search": False,
            "universe_search": False,
            "candidate_mutation": False,
            "strict_prior_only_signal": True,
        },
    }

    payload = json.dumps(result, sort_keys=True, indent=2).encode() + b"\n"
    result_sha = sha256_bytes(payload)
    (OUT / "HARMONY-FIN-0013-RESULT.json").write_bytes(payload)

    summary = {
        "experiment_id": "HARMONY-FIN-0013",
        "result_sha256": result_sha,
        "input_manifest_sha256": binding["manifest_sha256"],
        "normalized_panel_sha256": panel_sha,
        "full_trace": {"start": dates[0], "end": dates[-1], "observations": len(dates)},
        "first_rebalance": base["first_rebalance"],
        "oos_boundary": OOS_START,
        "strategy": base["metrics"],
        "benchmarks": {
            "same_universe_equal_weight_long_only": bench_equal["metrics"],
            "BTCUSDT_buy_and_hold": bench_btc["metrics"],
        },
        "predefined_cost_stress": stress,
        "holdout_access": False,
    }
    summary_bytes = json.dumps(summary, sort_keys=True, indent=2).encode() + b"\n"
    (OUT / "HARMONY-FIN-0013-SUMMARY.json").write_bytes(summary_bytes)

    reconciliation = {
        "experiment_id": "HARMONY-FIN-0013",
        "binding_id": binding["binding_id"],
        "candidate_digest": EXPECTED_CANDIDATE_DIGEST,
        "result_sha256": result_sha,
        "summary_sha256": sha256_bytes(summary_bytes),
        "input_manifest_sha256": binding["manifest_sha256"],
        "normalized_panel_sha256": panel_sha,
        "status": "RECONCILED_EXECUTION_EVIDENCE",
    }
    reconciliation_bytes = json.dumps(reconciliation, sort_keys=True, indent=2).encode() + b"\n"
    (OUT / "HARMONY-FIN-0013-RECONCILIATION.json").write_bytes(reconciliation_bytes)
    print(json.dumps(summary, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
