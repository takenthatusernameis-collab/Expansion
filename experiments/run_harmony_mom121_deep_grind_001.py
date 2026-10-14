#!/usr/bin/env python3
"""Preregistered Harmony MOM121 gate and cost-aware monthly backtest."""
from __future__ import annotations
import hashlib, json, math, os, shutil, urllib.request, zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("HARMONY_MOM121_DATA_DIR", ROOT / "data/cache/equities/mom121"))
OUT = ROOT / "artifacts/HARMONY-MOM121-DEEP-GRIND-001"
CAP_CEILING, MIN_PRICE, TOP_FRAC = 300_000_000.0, 1.0, 0.20
COSTS, PLACEBOS, BOOTSTRAPS, SEED = (25, 50, 100, 200), 250, 2000, 121
ASSERTIONS = (
    "point_in_time_universe", "includes_active_and_delisted_securities",
    "includes_delisting_returns_in_total_return", "total_returns_include_distributions",
    "market_cap_is_point_in_time", "stable_security_id",
)
COLUMNS = (
    "month_end", "security_id", "ticker", "exchange", "security_type",
    "is_active_at_month_end", "price", "market_cap_usd", "adv20_usd", "total_return",
)
EXCHANGES = {"NYSE", "NYSEAMERICAN", "AMEX", "NYSEMKT", "NASDAQ", "NASDAQGS", "NASDAQGM", "NASDAQCM"}
COMMON = {"COMMON", "COMMON_STOCK", "CS", "COMMON SHARE", "COMMON SHARES"}


class DataGateError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def get_bool(x: Any):
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    v = str(x).strip().lower()
    if v in {"true", "1", "yes", "y"}:
        return True
    if v in {"false", "0", "no", "n"}:
        return False
    return None


def acquire_archive() -> dict[str, Any]:
    url = os.environ.get("MOM121_DATA_ARCHIVE_URL", "").strip()
    expected = os.environ.get("MOM121_DATA_ARCHIVE_SHA256", "").strip().lower()
    if not url and not expected:
        return {"configured": False, "status": "not_configured"}
    if not url or len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise DataGateError("Both MOM121_DATA_ARCHIVE_URL and a valid MOM121_DATA_ARCHIVE_SHA256 are required")
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = OUT / "private_dataset_download.zip"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Harmony-MOM121-Research/1.0"})
        with urllib.request.urlopen(req, timeout=90) as resp, tmp.open("wb") as f:
            shutil.copyfileobj(resp, f)
        if digest(tmp) != expected:
            raise DataGateError("Checksum-pinned dataset archive failed SHA-256 verification")
        base = DATA.resolve()
        with zipfile.ZipFile(tmp) as zf:
            for member in zf.infolist():
                rel = PurePosixPath(member.filename)
                mode = member.external_attr >> 16
                if rel.is_absolute() or ".." in rel.parts or mode & 0o170000 == 0o120000:
                    raise DataGateError("Dataset archive contains unsafe path or symlink")
                dest = (DATA / Path(*rel.parts)).resolve()
                if base != dest and base not in dest.parents:
                    raise DataGateError("Dataset archive escapes data root")
            zf.extractall(DATA)
        return {"configured": True, "status": "downloaded_and_verified", "sha256": expected}
    finally:
        tmp.unlink(missing_ok=True)


def load_panel() -> tuple[pd.DataFrame, dict[str, Any]]:
    acquisition = acquire_archive()
    mf, pf = DATA / "manifest.json", DATA / "monthly_panel.csv"
    if not mf.is_file() or not pf.is_file():
        raise DataGateError(
            "No point-in-time US microcap panel found (manifest.json + monthly_panel.csv required). "
            "Harmony's verified shared cache is crypto/futures data, not a survivorship-safe US-equity dataset."
        )
    try:
        manifest = json.loads(mf.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DataGateError(f"manifest.json invalid: {exc}") from exc
    failed = [k for k in ASSERTIONS if manifest.get(k) is not True]
    if failed:
        raise DataGateError("Manifest fails required assertions: " + ", ".join(failed))
    file_meta = (manifest.get("files") or {}).get("monthly_panel.csv", {})
    if len(file_meta.get("sha256", "")) != 64 or digest(pf) != file_meta.get("sha256", "").lower():
        raise DataGateError("monthly_panel.csv SHA-256 missing or mismatched")
    try:
        df = pd.read_csv(pf, low_memory=False)
    except Exception as exc:
        raise DataGateError(f"monthly_panel.csv could not be parsed: {exc}") from exc
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise DataGateError("Required panel columns missing: " + ", ".join(missing))
    df = df.loc[:, COLUMNS].copy()
    df["month_end"] = pd.to_datetime(df["month_end"], errors="coerce")
    if df["month_end"].isna().any():
        raise DataGateError("Invalid month_end values")
    df["month"] = df["month_end"].dt.to_period("M")
    for col in ("security_id", "ticker", "exchange", "security_type"):
        if df[col].isna().any() or (df[col].astype(str).str.strip() == "").any():
            raise DataGateError(f"Blank {col} values")
        df[col] = df[col].astype(str).str.strip()
    if df.duplicated(["security_id", "month"]).any():
        raise DataGateError("Duplicate security_id/month records")
    for col in ("price", "market_cap_usd", "adv20_usd", "total_return"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
        if df[col].isna().any() or not np.isfinite(df[col].to_numpy()).all():
            raise DataGateError(f"Missing/non-finite {col}")
    if (df.price <= 0).any() or (df.market_cap_usd <= 0).any() or (df.adv20_usd < 0).any() or (df.total_return < -1).any():
        raise DataGateError("Invalid price, market cap, ADV or total return range")
    if df["is_active_at_month_end"].map(get_bool).isna().any():
        raise DataGateError("is_active_at_month_end must be boolean")
    df["is_active_at_month_end"] = df["is_active_at_month_end"].map(get_bool)
    df["exch"] = df.exchange.str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)
    df["stype"] = df.security_type.str.upper().str.strip()
    df = df.sort_values(["security_id", "month"]).reset_index(drop=True)
    months = sorted(df.month.unique())
    if len(months) < 13:
        raise DataGateError(f"Only {len(months)} months; need at least 13 for a 12-1 signal")
    return df, {
        "status": "passed", "acquisition": acquisition, "source": manifest.get("source"),
        "source_dataset_id": manifest.get("source_dataset_id"), "manifest_sha256": digest(mf),
        "monthly_panel_sha256": digest(pf), "rows": int(len(df)),
        "securities": int(df.security_id.nunique()), "first_month": str(months[0]),
        "last_month": str(months[-1]), "distinct_months": len(months),
        "point_in_time_universe": True, "delisting_returns_asserted": True,
    }


def compute_scores(df: pd.DataFrame) -> dict[tuple[pd.Period, str], float]:
    scores = {}
    for sid, grp in df.groupby("security_id", sort=False):
        g = grp.sort_values("month")
        months = g.month.tolist()
        returns = g.total_return.to_numpy(dtype=float)
        for i in range(11, len(g)):
            expected = list(pd.period_range(months[i] - 11, months[i] - 1, freq="M"))
            if months[i - 11:i] != expected:
                continue
            window = returns[i - 11:i]  # skip the most recently completed monthly return
            if np.isfinite(window).all() and (window >= -1.0).all():
                scores[(months[i], str(sid))] = float(np.prod(1.0 + window) - 1.0)
    return scores


def targets_for(df: pd.DataFrame, scores: dict):
    top, all_eligible, counts = {}, {}, {}
    for month, rows in df.groupby("month", sort=True):
        elig = rows[
            rows.is_active_at_month_end & rows.exch.isin(EXCHANGES) & rows.stype.isin(COMMON)
            & (rows.market_cap_usd <= CAP_CEILING) & (rows.price >= MIN_PRICE)
        ]
        ranked = []
        for r in elig.itertuples(index=False):
            key = (month, str(r.security_id))
            if key in scores:
                ranked.append((str(r.security_id), scores[key]))
        ranked.sort(key=lambda p: (-p[1], p[0]))
        ids = [p[0] for p in ranked]
        all_eligible[month] = ids
        counts[str(month)] = len(ids)
        k = min(len(ids), max(1, int(math.ceil(len(ids) * TOP_FRAC)))) if ids else 0
        top[month] = {sid: 1.0 / k for sid in ids[:k]} if k else {}
    equal = {m: ({sid: 1 / len(ids) for sid in ids} if ids else {}) for m, ids in all_eligible.items()}
    return top, equal, all_eligible, counts


def run_portfolio(df: pd.DataFrame, targets: dict, bps: float, capacity: bool = True) -> list[dict[str, Any]]:
    by_month = {m: g.set_index("security_id", drop=False) for m, g in df.groupby("month", sort=True)}
    months = sorted(m for m in targets if m in by_month)
    prev, cash, nav, records = {}, 1.0, 1_000_000.0, []
    for month in months:
        rows = by_month[month]
        gross, values, cash_value = 0.0, {}, cash
        for sid, weight in prev.items():
            if sid not in rows.index:
                raise DataGateError(f"Held security {sid} has no row at {month}; delisting coverage incomplete")
            row = rows.loc[sid]
            ret = float(row.total_return)
            value = weight * (1 + ret)
            gross += weight * ret
            if bool(row.is_active_at_month_end):
                values[sid] = value
            else:
                cash_value += value
        factor = cash_value + sum(values.values())
        if not math.isfinite(factor) or factor <= 0:
            raise DataGateError(f"Portfolio equity non-positive at {month}")
        pre = {sid: v / factor for sid, v in values.items()}
        target = targets.get(month, {})
        turnover = sum(abs(target.get(s, 0.0) - pre.get(s, 0.0)) for s in set(target) | set(pre))
        cost_fraction = bps / 10000.0 * turnover
        net = factor * (1.0 - cost_fraction) - 1.0
        orders = over = max_part = 0.0
        order_events = []
        if capacity:
            for sid in set(target) | set(pre):
                amount = abs(target.get(sid, 0.0) - pre.get(sid, 0.0)) * nav * factor
                orders += amount
                if amount <= 0:
                    continue
                adv = float(rows.loc[sid].adv20_usd) if sid in rows.index else 0.0
                order_events.append({"order_notional_usd": float(amount), "adv20_usd": adv})
                if adv <= 0:
                    over += amount
                    max_part = float("inf")
                else:
                    p = amount / adv
                    max_part = max(max_part, p)
                    if p > 0.01:
                        over += amount
        records.append({
            "month": str(month), "gross_return": float(gross), "net_return": float(net),
            "benchmark_net_return": 0.0, "turnover_abs": float(turnover),
            "cost_fraction": float(cost_fraction * factor), "order_notional_usd": float(orders),
            "order_notional_over_1pct_adv_usd": float(over),
            "max_order_participation_adv": max_part if math.isfinite(max_part) else None,
            "order_events": order_events,
            "holdings_count": len(target),
        })
        nav *= max(0.0, 1.0 + net)
        prev, cash = dict(target), max(0.0, 1.0 - sum(target.values()))
    return records


def pair_benchmark(strategy: list[dict], bench: list[dict]) -> list[dict]:
    b = {x["month"]: x for x in bench}
    result = []
    for row in strategy:
        if row["month"] in b:
            x = dict(row)
            x["benchmark_net_return"] = b[row["month"]]["net_return"]
            x["benchmark_gross_return"] = b[row["month"]]["gross_return"]
            result.append(x)
    return result


def metrics(values: list[dict], start: int = 0, end: int | None = None) -> dict[str, Any]:
    x = values[start:end]
    if not x:
        return {"months": 0}
    gross = np.array([r["gross_return"] for r in x])
    net = np.array([r["net_return"] for r in x])
    bench = np.array([r["benchmark_net_return"] for r in x])
    excess = net - bench
    wealth = np.cumprod(1 + net)
    peaks = np.maximum.accumulate(np.r_[1.0, wealth])[1:]
    dd = wealth / peaks - 1
    years = len(net) / 12.0
    std = float(np.std(net, ddof=1)) if len(net) > 1 else 0.0
    excess_std = float(np.std(excess, ddof=1)) if len(excess) > 1 else 0.0
    gw, nw = float(np.prod(1 + gross)), float(wealth[-1])
    return {
        "months": len(x), "gross_total_return": gw - 1, "net_total_return": nw - 1,
        "gross_cagr": gw ** (1 / years) - 1 if years and gw > 0 else None,
        "net_cagr": nw ** (1 / years) - 1 if years and nw > 0 else None,
        "net_sharpe": float(np.mean(net) / std * math.sqrt(12)) if std > 0 else 0.0,
        "net_excess_sharpe_vs_equal_weight": float(np.mean(excess) / excess_std * math.sqrt(12)) if excess_std > 0 else 0.0,
        "max_drawdown": float(dd.min()), "mean_monthly_excess_vs_equal_weight": float(np.mean(excess)),
        "positive_excess_month_fraction": float(np.mean(excess > 0)),
        "total_turnover_abs": float(sum(r["turnover_abs"] for r in x)),
        "cost_fraction_sum": float(sum(r["cost_fraction"] for r in x)),
    }


def capacity_diagnostics(records: list[dict], portfolio_sizes=(100_000, 1_000_000, 10_000_000)) -> dict[str, Any]:
    events = [e for row in records for e in row.get("order_events", [])]
    result = {}
    for size in portfolio_sizes:
        scale = float(size) / 1_000_000.0
        total = sum(float(e["order_notional_usd"]) * scale for e in events)
        over = 0.0
        max_participation = 0.0
        for event in events:
            amount = float(event["order_notional_usd"]) * scale
            adv = float(event["adv20_usd"])
            if amount <= 0:
                continue
            if adv <= 0:
                over += amount
                max_participation = float("inf")
            else:
                participation = amount / adv
                max_participation = max(max_participation, participation)
                if participation > 0.01:
                    over += amount
        result[str(size)] = {
            "portfolio_capital_usd": int(size),
            "fraction_order_notional_over_1pct_adv": float(over / total) if total else 0.0,
            "max_observed_order_participation_adv": max_participation if math.isfinite(max_participation) else None,
            "total_order_notional_usd": float(total),
        }
    return result


def bootstrap_ci(excess: np.ndarray) -> dict[str, Any]:
    if len(excess) < 12:
        return {"replications": 0, "block_length_months": 12, "observed_mean": float(np.mean(excess)) if len(excess) else None, "ci95_lower": None, "ci95_upper": None, "one_sided_p": None}
    rng, n, observed = np.random.default_rng(SEED), len(excess), float(np.mean(excess))
    centered, out = excess - observed, np.empty(BOOTSTRAPS)
    for b in range(BOOTSTRAPS):
        starts = rng.integers(0, n, size=int(math.ceil(n / 12)))
        idx = np.concatenate([(s + np.arange(12)) % n for s in starts])[:n]
        out[b] = float(np.mean(centered[idx]))
    return {
        "replications": BOOTSTRAPS, "block_length_months": 12, "observed_mean": observed,
        "ci95_lower": float(observed + np.quantile(out, .025)),
        "ci95_upper": float(observed + np.quantile(out, .975)),
        "one_sided_p": float((1 + np.sum(out >= observed)) / (BOOTSTRAPS + 1)),
    }


def placebo_targets(eligible: dict, seed: int) -> dict:
    rng, result = np.random.default_rng(seed), {}
    for month, ids in eligible.items():
        k = min(len(ids), max(1, int(math.ceil(len(ids) * TOP_FRAC)))) if ids else 0
        chosen = rng.choice(np.array(ids, dtype=object), k, replace=False).tolist() if k else []
        result[month] = {str(s): 1.0 / k for s in chosen} if k else {}
    return result


def run_backtest(df: pd.DataFrame, data_meta: dict) -> dict[str, Any]:
    scores = compute_scores(df)
    top, equal, eligible, counts = targets_for(df, scores)
    if sum(bool(v) for v in top.values()) < 13:
        raise DataGateError("Too few months with eligible microcap securities and valid 12-1 signals")
    valid_months = [m for m, ids in eligible.items() if ids]
    if not valid_months:
        raise DataGateError("No eligible point-in-time microcap months after applying the fixed universe and signal rules")
    first_valid_month = min(valid_months)
    top = {m: v for m, v in top.items() if m >= first_valid_month}
    equal = {m: v for m, v in equal.items() if m >= first_valid_month}
    eligible = {m: v for m, v in eligible.items() if m >= first_valid_month}
    counts = {str(m): len(ids) for m, ids in eligible.items()}
    data_meta["usable_strategy_months"] = len(top)
    stress, base_rows = {}, []
    for bps in COSTS:
        strat = run_portfolio(df, top, bps, True)
        bench = run_portfolio(df, equal, bps, False)
        paired = pair_benchmark(strat, bench)
        stress[str(int(bps))] = metrics(paired)
        if bps == 50:
            base_rows = paired
    n = len(base_rows)
    c1, c2 = int(n * .60), int(n * .80)
    chrono = {
        "discovery_first_60pct": metrics(base_rows, 0, c1),
        "validation_next_20pct": metrics(base_rows, c1, c2),
        "final_test_last_20pct": metrics(base_rows, c2, n),
        "final_test_first_half": metrics(base_rows, c2, c2 + (n - c2) // 2),
        "final_test_second_half": metrics(base_rows, c2 + (n - c2) // 2, n),
    }
    test = base_rows[c2:]
    excess = np.array([r["net_return"] - r["benchmark_net_return"] for r in test])
    boot = bootstrap_ci(excess)
    test_excess_sharpe = metrics(test).get("net_excess_sharpe_vs_equal_weight", 0.0)
    bench_base = run_portfolio(df, equal, 50, False)
    placebo_scores = []
    rng = np.random.default_rng(SEED)
    for _ in range(PLACEBOS):
        p = run_portfolio(df, placebo_targets(eligible, int(rng.integers(0, 2**31 - 1))), 50, False)
        placebo_scores.append(float(metrics(pair_benchmark(p, bench_base), c2).get("net_excess_sharpe_vs_equal_weight", 0.0)))
    p95 = float(np.quantile(placebo_scores, .95))
    placebo_p = float((1 + sum(v >= test_excess_sharpe for v in placebo_scores)) / (len(placebo_scores) + 1))
    capacity = capacity_diagnostics(base_rows)
    over_share = capacity["1000000"]["fraction_order_notional_over_1pct_adv"]
    history_months, test_months = int(data_meta.get("usable_strategy_months", data_meta["distinct_months"])), len(test)
    gates = {
        "history_at_least_120_months": history_months >= 120,
        "final_test_at_least_36_months": test_months >= 36,
        "positive_final_test_mean_net_excess_at_50bps": bool(len(excess) and np.mean(excess) > 0),
        "bootstrap_95pct_lower_bound_positive": bool(boot["ci95_lower"] is not None and boot["ci95_lower"] > 0),
        "above_95th_random_ranking_placebo": test_excess_sharpe > p95,
        "at_least_95pct_order_notional_within_1pct_adv_at_1m": over_share <= .05,
        "positive_excess_in_both_final_test_halves": (
            chrono["final_test_first_half"].get("mean_monthly_excess_vs_equal_weight", -1) > 0
            and chrono["final_test_second_half"].get("mean_monthly_excess_vs_equal_weight", -1) > 0
        ),
    }
    if history_months < 120 or test_months < 36:
        status = "exploratory_only"
    elif all(gates.values()):
        status = "eligible_for_independent_confirmation"
    elif not gates["positive_final_test_mean_net_excess_at_50bps"] and not gates["positive_excess_in_both_final_test_halves"]:
        status = "rejected"
    else:
        status = "inconclusive"
    monthly = [{
        "month": r["month"], "strategy_gross_return": r["gross_return"],
        "strategy_net_return_50bps": r["net_return"],
        "benchmark_gross_return": r["benchmark_gross_return"],
        "benchmark_net_return_50bps": r["benchmark_net_return"],
        "excess_net_return_50bps": r["net_return"] - r["benchmark_net_return"],
        "turnover_abs": r["turnover_abs"], "cost_fraction": r["cost_fraction"],
        "order_notional_usd_at_1m": r["order_notional_usd"],
        "order_notional_over_1pct_adv_usd_at_1m": r["order_notional_over_1pct_adv_usd"],
        "max_order_participation_adv_at_1m": r["max_order_participation_adv"],
        "holdings_count": r["holdings_count"],
    } for r in base_rows]
    pd.DataFrame(monthly).to_csv(OUT / "monthly_returns.csv", index=False)
    return {
        "experiment_id": "HARMONY-MOM121-DEEP-GRIND-001", "status": status, "data": data_meta,
        "fixed_strategy": {"market_cap_ceiling_usd": CAP_CEILING, "minimum_price_usd": MIN_PRICE,
            "signal_observations": 11, "skip_recent_month": True, "portfolio": "equal-weight top quintile",
            "base_cost_one_way_bps": 50, "cost_stress_bps": list(COSTS), "seed": SEED},
        "eligible_counts_by_month": counts, "metrics_by_cost_bps": stress,
        "chronological_metrics_at_50bps": chrono, "final_test_bootstrap": boot,
        "placebo": {"replications": PLACEBOS, "seed": SEED,
            "final_test_net_excess_sharpe_vs_equal_weight": test_excess_sharpe,
            "random_rank_net_excess_sharpe_95th_percentile": p95, "empirical_one_sided_p": placebo_p},
        "capacity_by_portfolio_size": capacity, "gates": gates,
        "limitations": ["Historical simulation does not prove future profitability.",
            "Manifest assertions require independent source audit.", "ADV participation is a capacity diagnostic, not a calibrated impact model.",
            "Borrow availability is not modeled because the primary candidate is long-only."],
    }


def write_blocked(reason: str) -> None:
    result = {"experiment_id": "HARMONY-MOM121-DEEP-GRIND-001", "status": "blocked_data_gate",
        "timestamp_utc": utc_now(), "reason": reason, "required_files": ["manifest.json", "monthly_panel.csv"],
        "required_assertions": list(ASSERTIONS), "required_columns": list(COLUMNS), "no_backtest_metrics_emitted": True}
    write_json(OUT / "data_gate.json", result)
    write_json(OUT / "summary.json", result)
    (OUT / "report.md").write_text(
        "# Harmony MOM121 Deep Grind 001\n\n## Status: BLOCKED — DATA GATE\n\n"
        f"**Reason:** {reason}\n\n"
        "No strategy performance metrics were produced. This is not a positive or negative finding about momentum. "
        "Supply a checksum-pinned, point-in-time US microcap panel containing active and inactive securities, "
        "total returns including delisting returns, point-in-time market caps and 20-session ADV. "
        "A current surviving-ticker universe is not an acceptable substitute.\n", encoding="utf-8")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    write_json(OUT / "run_manifest.json", {
        "experiment_id": "HARMONY-MOM121-DEEP-GRIND-001", "started_at_utc": utc_now(),
        "git_sha": os.environ.get("GITHUB_SHA"), "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
        "workflow_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"), "python": os.sys.version,
        "numpy_version": np.__version__, "pandas_version": pd.__version__, "seed": SEED,
        "protocol_sha256": digest(ROOT / "experiments/HARMONY-MOM121-DEEP-GRIND-001.yaml"),
        "script_sha256": digest(Path(__file__)),
    })
    try:
        panel, meta = load_panel()
        write_json(OUT / "data_gate.json", meta)
        summary = run_backtest(panel, meta)
        summary["completed_at_utc"] = utc_now()
        write_json(OUT / "summary.json", summary)
        (OUT / "report.md").write_text(
            "# Harmony MOM121 Deep Grind 001\n\n"
            f"## Status: {summary['status']}\n\n"
            f"Data window: {meta['first_month']} to {meta['last_month']}; {meta['rows']} rows, {meta['securities']} securities.\n\n"
            "Detailed results, cost stresses, chronology, bootstrap, placebo and capacity gates are in summary.json. "
            "Monthly net strategy and matched benchmark returns are in monthly_returns.csv.\n", encoding="utf-8")
        return 0
    except DataGateError as exc:
        write_blocked(str(exc))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
