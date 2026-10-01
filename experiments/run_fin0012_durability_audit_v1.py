import csv
import hashlib
import io
import json
import math
import statistics
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SYMBOLS = ["BTCUSDT", "ETHUSDT", "LTCUSDT", "XRPUSDT", "BNBUSDT", "BCHUSDT", "ADAUSDT", "DOGEUSDT"]
ROOT = Path("data/cache/binance/futures_um/monthly")
OUT = Path("artifacts/HARMONY-FIN-0012-DURABILITY-V1")
START = "2021-01-01"
END = "2025-10-31"
OOS_START = "2024-05-22"
FEE = 0.0006
SLIP = 0.0005
WINDOW = 60

ACCEPTED = {
    "cumulative_return": 0.4169009579470373,
    "cagr": 0.273179006712682,
    "sharpe": 0.9706848261971941,
    "max_drawdown": -0.2791530147083816,
    "turnover": 37.25,
    "transaction_costs": 0.08304999999999994,
    "funding_pnl": 0.005558342500000007,
}

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def months():
    out = []
    y, m = 2021, 1
    while (y, m) <= (2025, 10):
        out.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out

def read_zip(path):
    with zipfile.ZipFile(path) as z:
        if z.testzip() is not None:
            raise RuntimeError(f"CRC failure: {path}")
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise RuntimeError(f"unexpected archive members: {path}")
        return list(csv.reader(io.StringIO(z.read(names[0]).decode("utf-8"))))

def day(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).date().isoformat()

def load_inputs():
    px = {s: {} for s in SYMBOLS}
    funding = {s: {} for s in SYMBOLS}
    hashes = []
    for s in SYMBOLS:
        for y, m in months():
            kp = ROOT / "klines" / s / "1d" / f"{s}-1d-{y:04d}-{m:02d}.zip"
            fp = ROOT / "fundingRate" / s / f"{s}-fundingRate-{y:04d}-{m:02d}.zip"
            for p in (kp, fp):
                raw = p.read_bytes()
                hashes.append({"path": str(p), "bytes": len(raw), "sha256": sha(raw)})
            for row in read_zip(kp):
                if row and row[0].isdigit():
                    px[s][day(int(row[0]))] = float(row[4])
            rows = read_zip(fp)
            h = {k.strip(): i for i, k in enumerate(rows[0])}
            for row in rows[1:]:
                if row:
                    d = day(int(row[h["calc_time"]]))
                    funding[s].setdefault(d, []).append(float(row[h["last_funding_rate"]]))
    dates = sorted(set.intersection(*(set(px[s]) for s in SYMBOLS)))
    if not dates or dates[0] != START or dates[-1] != END:
        raise RuntimeError(f"unexpected common panel: {dates[0] if dates else None}..{dates[-1] if dates else None}")
    return px, funding, dates, hashes

def metrics(returns):
    eq = [1.0]
    for r in returns:
        eq.append(eq[-1] * (1.0 + r))
    sd = statistics.stdev(returns) if len(returns) > 1 else 0.0
    sharpe = statistics.mean(returns) / sd * math.sqrt(365.25) if sd else 0.0
    downside = [min(0.0, r) for r in returns]
    dsd = statistics.stdev(downside) if len(downside) > 1 else 0.0
    sortino = statistics.mean(returns) / dsd * math.sqrt(365.25) if dsd else 0.0
    peak = eq[0]
    mdd = 0.0
    for x in eq:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1.0)
    years = max(len(returns) / 365.25, 1e-12)
    return {
        "cumulative_return": eq[-1] - 1.0,
        "cagr": eq[-1] ** (1.0 / years) - 1.0,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": mdd,
        "final_equity": eq[-1],
    }

def target(px, dates, i):
    if i < 61:
        return None
    signal_idx = i - 1
    base_idx = i - 21
    btc20 = px["BTCUSDT"][dates[signal_idx]] / px["BTCUSDT"][dates[base_idx]] - 1.0
    btc_daily = [
        px["BTCUSDT"][dates[j]] / px["BTCUSDT"][dates[j - 1]] - 1.0
        for j in range(i - 60, i)
    ]
    btc_mean = sum(btc_daily) / 60.0
    btc_var = sum((r - btc_mean) ** 2 for r in btc_daily) / 60.0
    scores = []
    for s in SYMBOLS:
        asset_return = px[s][dates[signal_idx]] / px[s][dates[base_idx]] - 1.0
        asset_daily = [
            px[s][dates[j]] / px[s][dates[j - 1]] - 1.0
            for j in range(i - 60, i)
        ]
        asset_mean = sum(asset_daily) / 60.0
        covariance = sum(
            (x - asset_mean) * (y - btc_mean)
            for x, y in zip(asset_daily, btc_daily)
        ) / 60.0
        beta = covariance / btc_var if btc_var else 0.0
        scores.append((asset_return - beta * btc20, s))
    scores.sort(key=lambda x: (-x[0], x[1]))
    weights = {s: 0.0 for s in SYMBOLS}
    for _, s in scores[:2]:
        weights[s] = 0.25
    for _, s in scores[-2:]:
        weights[s] = -0.25
    return weights

def simulate(px, funding, dates, start_idx, end_idx, cost_mult=1.0):
    weights = {s: 0.0 for s in SYMBOLS}
    equity = 1.0
    returns = []
    equity_curve = []
    turnover = 0.0
    transaction_costs = 0.0
    funding_pnl = 0.0

    for i in range(start_idx, end_idx):
        d = dates[i]

        # Exact FIN-0012 execution order:
        # funding -> daily price PnL -> rebalance -> transaction cost.
        if i > start_idx:
            previous_date = dates[i - 1]
            price_pnl = sum(
                weights[s] * (px[s][d] / px[s][previous_date] - 1.0)
                for s in SYMBOLS
            )
            funding_cash = -sum(
                weights[s] * sum(funding[s].get(d, []))
                for s in SYMBOLS
            )
            daily = price_pnl + funding_cash
            funding_pnl += funding_cash
            equity *= 1.0 + daily
        else:
            daily = 0.0

        if (i - start_idx) % 7 == 0:
            new_weights = target(px, dates, i)
            if new_weights is not None:
                delta = sum(abs(new_weights[s] - weights[s]) for s in SYMBOLS)
                cost = (FEE + SLIP) * cost_mult * delta
                equity *= 1.0 - cost
                transaction_costs += cost
                turnover += delta / 2.0
                weights = new_weights

        returns.append(daily)
        equity_curve.append(equity)

    liquidation = sum(abs(v) for v in weights.values())
    liquidation_cost = (FEE + SLIP) * cost_mult * liquidation
    equity *= 1.0 - liquidation_cost
    transaction_costs += liquidation_cost
    equity_curve[-1] = equity

    exact_returns = [equity_curve[0] - 1.0]
    exact_returns.extend(
        equity_curve[i] / equity_curve[i - 1] - 1.0
        for i in range(1, len(equity_curve))
    )
    return {
        "dates": dates[start_idx:end_idx],
        "returns": exact_returns,
        "equity": equity_curve,
        "turnover": turnover,
        "transaction_costs": transaction_costs,
        "funding_pnl": funding_pnl,
    }

def benchmark_returns(px, dates, start_idx, kind):
    weights = {s: 0.0 for s in SYMBOLS}
    if kind == "btc":
        weights["BTCUSDT"] = 1.0
    else:
        for s in SYMBOLS:
            weights[s] = 1.0 / len(SYMBOLS)
    out = [0.0]
    for i in range(start_idx + 1, len(dates)):
        d = dates[i]
        p = dates[i - 1]
        out.append(sum(weights[s] * (px[s][d] / px[s][p] - 1.0) for s in SYMBOLS))
    return out

def rolling_attribution(strategy, btc, ew, window=60):
    residual = [None] * len(strategy)
    betas = [None] * len(strategy)
    r2 = [None] * len(strategy)
    for i in range(window, len(strategy)):
        y = strategy[i - window:i]
        x = btc[i - window:i]
        z = ew[i - window:i]
        mx, mz, my = statistics.mean(x), statistics.mean(z), statistics.mean(y)
        s11 = sum((v - mx) ** 2 for v in x)
        s22 = sum((v - mz) ** 2 for v in z)
        s12 = sum((u - mx) * (v - mz) for u, v in zip(x, z))
        sy1 = sum((u - mx) * (v - my) for u, v in zip(x, y))
        sy2 = sum((u - mz) * (v - my) for u, v in zip(z, y))
        det = s11 * s22 - s12 * s12
        if abs(det) < 1e-18:
            b1 = b2 = 0.0
        else:
            b1 = (sy1 * s22 - sy2 * s12) / det
            b2 = (sy2 * s11 - sy1 * s12) / det
        intercept = my - b1 * mx - b2 * mz
        predicted = intercept + b1 * btc[i] + b2 * ew[i]
        residual[i] = strategy[i] - predicted
        total = sum((v - my) ** 2 for v in y)
        error = sum(
            (v - (intercept + b1 * u + b2 * v2)) ** 2
            for v, u, v2 in zip(y, x, z)
        )
        r2[i] = 1.0 - error / total if total else 0.0
        betas[i] = (b1, b2)
    return residual, betas, r2

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    px, funding, dates, hashes = load_inputs()
    start_idx = dates.index(OOS_START)
    end_idx = len(dates)

    baseline = simulate(px, funding, dates, start_idx, end_idx, 1.0)
    base_metrics = metrics(baseline["returns"])

    checks = {
        "cumulative_return": (base_metrics["cumulative_return"], ACCEPTED["cumulative_return"]),
        "cagr": (base_metrics["cagr"], ACCEPTED["cagr"]),
        "sharpe": (base_metrics["sharpe"], ACCEPTED["sharpe"]),
        "max_drawdown": (base_metrics["max_drawdown"], ACCEPTED["max_drawdown"]),
        "turnover": (baseline["turnover"], ACCEPTED["turnover"]),
        "transaction_costs": (baseline["transaction_costs"], ACCEPTED["transaction_costs"]),
        "funding_pnl": (baseline["funding_pnl"], ACCEPTED["funding_pnl"]),
    }
    mismatches = {
        k: {"observed": obs, "accepted": exp}
        for k, (obs, exp) in checks.items()
        if abs(obs - exp) > 1e-9
    }
    if mismatches:
        raise RuntimeError("exact reproduction gate failed: " + json.dumps(mismatches, sort_keys=True))

    btc = benchmark_returns(px, dates, start_idx, "btc")
    ew = benchmark_returns(px, dates, start_idx, "ew")
    residual, betas, r2 = rolling_attribution(baseline["returns"], btc, ew)

    n = len(baseline["returns"])
    q = n // 4
    segments = [
        ("half1", 0, n // 2),
        ("half2", n // 2, n),
        ("quarter1", 0, q),
        ("quarter2", q, 2 * q),
        ("quarter3", 2 * q, 3 * q),
        ("quarter4", 3 * q, n),
    ]
    segment_rows = []
    for name, a, b in segments:
        rr = baseline["returns"][a:b]
        resid = [x for x in residual[a:b] if x is not None]
        row = {
            "segment": name,
            "start": baseline["dates"][a],
            "end": baseline["dates"][b - 1],
            "observations": len(rr),
            **metrics(rr),
            "residual_cumulative_return": "" if not resid else metrics(resid)["cumulative_return"],
            "residual_sharpe": "" if not resid else metrics(resid)["sharpe"],
        }
        segment_rows.append(row)

    def rolling(window):
        out = []
        for i in range(window, n + 1):
            mm = metrics(baseline["returns"][i - window:i])
            out.append({
                "end": baseline["dates"][i - 1],
                **mm,
            })
        return out

    roll90 = rolling(90)
    roll180 = rolling(180)

    factor_quarters = []
    for qidx in range(4):
        a = qidx * q
        b = (qidx + 1) * q if qidx < 3 else n
        rr = [x for x in residual[a:b] if x is not None]
        factor_quarters.append({
            "quarter": qidx + 1,
            "start": baseline["dates"][a],
            "end": baseline["dates"][b - 1],
            **(metrics(rr) if rr else {}),
        })

    cost_stress = {}
    for mult in (1.0, 1.5, 2.0):
        run = simulate(px, funding, dates, start_idx, end_idx, mult)
        rb, _, _ = rolling_attribution(run["returns"], btc, ew)
        rr = [x for x in rb[WINDOW:] if x is not None]
        cost_stress[f"{mult:.1f}x"] = {
            "raw": metrics(run["returns"]),
            "residual": metrics(rr) if rr else {},
        }

    by_year = {}
    by_quarter = {}
    for d, r in zip(baseline["dates"], baseline["returns"]):
        by_year.setdefault(d[:4], []).append(r)
        month = int(d[5:7])
        qkey = f"{d[:4]}-Q{((month - 1) // 3) + 1}"
        by_quarter.setdefault(qkey, []).append(r)

    year_metrics = {k: metrics(v) for k, v in by_year.items()}
    quarter_metrics = {k: metrics(v) for k, v in by_quarter.items()}
    total_profit = base_metrics["cumulative_return"]
    best_year = max(year_metrics.items(), key=lambda kv: kv[1]["cumulative_return"])
    best_quarter = max(quarter_metrics.items(), key=lambda kv: kv[1]["cumulative_return"])

    manifest = {
        "cache_key": "harmony-binance-um-2021-01-2025-10-v4",
        "files": sorted(hashes, key=lambda x: x["path"]),
        "oos_start": OOS_START,
        "oos_end": END,
        "holdout_access": False,
        "candidate_mutation": False,
        "parameter_search": False,
        "accepted_result_checks": checks,
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True, indent=2).encode() + b"\n"
    (OUT / "input_manifest.json").write_bytes(manifest_bytes)

    residual_full = [x for x in residual[WINDOW:] if x is not None]
    rolling_summary = {
        "90": {
            "median_sharpe": statistics.median(x["sharpe"] for x in roll90),
            "q25_sharpe": sorted(x["sharpe"] for x in roll90)[len(roll90) // 4],
            "q75_sharpe": sorted(x["sharpe"] for x in roll90)[3 * len(roll90) // 4],
            "positive_return_fraction": sum(x["cumulative_return"] > 0 for x in roll90) / len(roll90),
            "positive_sharpe_fraction": sum(x["sharpe"] > 0 for x in roll90) / len(roll90),
        },
        "180": {
            "median_sharpe": statistics.median(x["sharpe"] for x in roll180),
            "q25_sharpe": sorted(x["sharpe"] for x in roll180)[len(roll180) // 4],
            "q75_sharpe": sorted(x["sharpe"] for x in roll180)[3 * len(roll180) // 4],
            "positive_return_fraction": sum(x["cumulative_return"] > 0 for x in roll180) / len(roll180),
            "positive_sharpe_fraction": sum(x["sharpe"] > 0 for x in roll180) / len(roll180),
        },
    }

    if segment_rows[0]["sharpe"] > 0 and segment_rows[1]["sharpe"] > 0 and \
       all(x["sharpe"] > 0 for x in factor_quarters) and cost_stress["2.0x"]["raw"]["sharpe"] > 0:
        status = "DURABILITY_SUPPORTED"
    elif metrics(residual_full)["sharpe"] > 0:
        status = "DURABILITY_MIXED"
    else:
        status = "DURABILITY_NOT_SUPPORTED"

    payload = {
        "version": "1.0",
        "status": status,
        "exact_reproduction": checks,
        "raw_oos": base_metrics,
        "residual_full_oos": metrics(residual_full),
        "segments": segment_rows,
        "factor_quarters": factor_quarters,
        "rolling": rolling_summary,
        "cost_stress": cost_stress,
        "timing": {
            "by_year": year_metrics,
            "by_quarter": quarter_metrics,
            "best_year": best_year[0],
            "best_year_contribution_fraction": best_year[1]["cumulative_return"] / total_profit if total_profit > 0 else None,
            "best_quarter": best_quarter[0],
            "best_quarter_contribution_fraction": best_quarter[1]["cumulative_return"] / total_profit if total_profit > 0 else None,
        },
        "integrity": {
            "candidate_immutable": True,
            "holdout_access": False,
            "parameter_search": False,
            "universe_search": False,
        },
    }
    (OUT / "durability_audit.json").write_text(json.dumps({
        **payload,
        "input_manifest_sha256": sha(manifest_bytes),
    }, sort_keys=True, indent=2) + "\n")

    for filename, rows in (
        ("oos_segments.csv", segment_rows),
        ("rolling_90.csv", roll90),
        ("rolling_180.csv", roll180),
    ):
        with (OUT / filename).open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    with (OUT / "equity_and_residual.csv").open("w", newline="") as f:
        fields = [
            "date", "strategy_equity", "strategy_return", "residual_return",
            "residual_equity", "rolling_btc_beta", "rolling_equal_weight_beta",
            "rolling_r2",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        residual_equity = 1.0
        for i, d in enumerate(baseline["dates"]):
            if residual[i] is not None:
                residual_equity *= 1.0 + residual[i]
            writer.writerow({
                "date": d,
                "strategy_equity": baseline["equity"][i],
                "strategy_return": baseline["returns"][i],
                "residual_return": "" if residual[i] is None else residual[i],
                "residual_equity": "" if residual[i] is None else residual_equity,
                "rolling_btc_beta": "" if betas[i] is None else betas[i][0],
                "rolling_equal_weight_beta": "" if betas[i] is None else betas[i][1],
                "rolling_r2": "" if r2[i] is None else r2[i],
            })

    (OUT / "cost_stress.json").write_text(json.dumps(cost_stress, sort_keys=True, indent=2) + "\n")

    report = [
        "# FIN-0012 Alpha Durability Audit v1",
        "",
        f"Research status: {status}",
        "",
        "Exact reproduction gate: PASS.",
        "",
        "FIN-0012 remains immutable. No holdout data, parameter search, universe search, or candidate mutation was used.",
        "",
        "## Fixed OOS decomposition",
        json.dumps(segment_rows, indent=2, sort_keys=True),
        "",
        "## Residual durability",
        json.dumps({"full": metrics(residual_full), "quarters": factor_quarters}, indent=2, sort_keys=True),
        "",
        "## Rolling durability",
        json.dumps(rolling_summary, indent=2, sort_keys=True),
        "",
        "## Cost durability",
        json.dumps(cost_stress, indent=2, sort_keys=True),
        "",
        "## Timing concentration",
        json.dumps(payload["timing"], indent=2, sort_keys=True),
        "",
        "## Next research action",
        "Do not create a descendant inside this audit. Use the status only to decide whether a separate frozen factor-neutral descendant should be preregistered.",
    ]
    (OUT / "durability_report.md").write_text("\n".join(report) + "\n")

    print(json.dumps({
        "status": status,
        "raw_sharpe": base_metrics["sharpe"],
        "residual_sharpe": metrics(residual_full)["sharpe"],
        "best_year_fraction": payload["timing"]["best_year_contribution_fraction"],
        "best_quarter_fraction": payload["timing"]["best_quarter_contribution_fraction"],
    }, indent=2))

if __name__ == "__main__":
    main()
