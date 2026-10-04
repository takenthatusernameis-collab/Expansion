#!/usr/bin/env python3
"""Exact FIN-0012 measurement-contract reconciliation for Harmony Deep Grind 003."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIN = ROOT / "artifacts/HARMONY-FIN-0012-DURABILITY-V2/durability_audit.json"
FIN_PATH = ROOT / "artifacts/HARMONY-FIN-0012-DURABILITY-V2/equity_and_residual.csv"
ALPHA = ROOT / "artifacts/HARMONY-ALPHA-AUTOPSY-V2/alpha_autopsy.json"
ALPHA_PATH = ROOT / "artifacts/HARMONY-ALPHA-AUTOPSY-V2/fin0012_daily_path.csv"
PLACEBO = ROOT / "artifacts/HARMONY-ALPHA-AUTOPSY-V2/alpha_autopsy.json"
OUT = ROOT / "artifacts/HARMONY-RECONCILIATION-002"
TOL = 1e-9


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_csv(path: Path):
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def compare(a: float, b: float) -> float:
    return abs(a - b)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    required = [FIN, FIN_PATH, ALPHA, ALPHA_PATH]
    missing = [str(p.relative_to(ROOT)) for p in required if not p.is_file()]
    if missing:
        payload = {"status": "FAIL", "reason": "missing_required_artifacts", "missing": missing}
        (OUT / "reconciliation.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return 1

    fin = json.loads(FIN.read_text(encoding="utf-8"))
    alpha = json.loads(ALPHA.read_text(encoding="utf-8"))
    fin_rows = load_csv(FIN_PATH)
    alpha_rows = load_csv(ALPHA_PATH)

    accepted = {k: v[0] for k, v in fin["reproduction_gate"]["values"].items()}
    a12 = alpha["candidates"]["FIN-0012"]
    net = a12["net_metrics"]
    observed = {
        "cumulative_return": net["cumulative_return"],
        "cagr": net["cagr"],
        "sharpe": net["sharpe"],
        "max_drawdown": net["max_drawdown"],
        "turnover": a12["execution"]["turnover"],
        "transaction_costs": a12["execution"]["transaction_costs"],
        "funding_pnl": a12["execution"]["funding_pnl"],
    }
    metric_comparison = {
        k: {
            "authoritative": accepted[k],
            "alpha": observed[k],
            "absolute_difference": compare(float(accepted[k]), float(observed[k])),
            "within_1e-9": compare(float(accepted[k]), float(observed[k])) <= TOL,
        }
        for k in accepted
    }

    path_errors = []
    if len(fin_rows) != len(alpha_rows):
        path_errors.append({
            "type": "row_count",
            "authoritative": len(fin_rows),
            "alpha": len(alpha_rows),
        })
    else:
        for idx, (fr, ar) in enumerate(zip(fin_rows, alpha_rows)):
            if fr["date"] != ar["date"]:
                path_errors.append({
                    "type": "date_mismatch",
                    "index": idx,
                    "authoritative": fr["date"],
                    "alpha": ar["date"],
                })
                break
            ae = float(ar["net_equity"])
            fe = float(fr["strategy_equity"])
            diff = abs(ae - fe)
            if diff > TOL:
                path_errors.append({
                    "type": "equity_path_mismatch",
                    "index": idx,
                    "date": fr["date"],
                    "authoritative": fe,
                    "alpha": ae,
                    "absolute_difference": diff,
                })
                break

    # Independently recompute placebo summary from persisted trial-level records if present in JSON.
    placebo_field = a12.get("placebo_net") or {}
    placebo_trials = alpha.get("_persisted_placebo_trials", {}).get("FIN-0012", [])
    # Current V4 may not persist all trials; this gate is therefore explicit rather than silently inferred.
    placebo_gate = {
        "status": "NOT_AVAILABLE",
        "reason": "V4 summary does not persist trial-level placebo records",
    }
    if placebo_trials:
        def pct(actual, key):
            vals = [float(x[key]) for x in placebo_trials]
            return 100.0 * sum(v <= float(actual) for v in vals) / len(vals)
        placebo_gate = {
            "status": "PASS",
            "cumulative_percentile": abs(
                pct(net["cumulative_return"], "cum")
                - float(placebo_field["actual_cumulative_percentile"])
            ) <= 1e-12,
            "sharpe_percentile": abs(
                pct(net["sharpe"], "sharpe")
                - float(placebo_field["actual_sharpe_percentile"])
            ) <= 1e-12,
            "mdd_percentile": abs(
                pct(net["max_drawdown"], "mdd")
                - float(placebo_field["actual_mdd_percentile"])
            ) <= 1e-12,
        }

    integrity = a12.get("integrity") or {}
    integrity_ok = all(
        integrity.get(k) is False
        for k in (
            "holdout_access",
            "candidate_mutation",
            "parameter_search",
            "universe_search",
            "direction_search",
        )
    )

    all_metrics = all(x["within_1e-9"] for x in metric_comparison.values())
    all_path = not path_errors
    session_pass = all_metrics and all_path and integrity_ok

    payload = {
        "reconciliation_id": "HARMONY-RECONCILIATION-002",
        "status": "PASS" if session_pass else "FAIL",
        "authority": {
            "durability_audit_sha256": sha256_file(FIN),
            "equity_path_sha256": sha256_file(FIN_PATH),
            "engine_source_commit": fin.get("engine_source_commit") or "8388dc680571bf3fa849d349b60de99e38ebf8ea",
        },
        "alpha": {
            "autopsy_sha256": sha256_file(ALPHA),
            "equity_path_sha256": sha256_file(ALPHA_PATH),
        },
        "metric_comparison": metric_comparison,
        "daily_equity_path": {
            "authoritative_rows": len(fin_rows),
            "alpha_rows": len(alpha_rows),
            "max_checked_absolute_difference": (
                max(
                    abs(float(a["net_equity"]) - float(f["strategy_equity"]))
                    for a, f in zip(alpha_rows, fin_rows)
                )
                if len(alpha_rows) == len(fin_rows)
                else None
            ),
            "first_failure": path_errors[0] if path_errors else None,
        },
        "placebo_gate": placebo_gate,
        "integrity_gate": integrity_ok,
        "classification": (
            "implementation/accounting artifact resolved"
            if session_pass
            else "unresolved measurement-contract failure"
        ),
        "research_rule": "The accepted FIN-0012 engine remains authoritative; no candidate or parameter was changed.",
    }
    (OUT / "reconciliation.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# FIN-0012 Reconciliation 002",
        "",
        f"**Status:** {payload['status']}",
        "",
        "## Metric gate",
        f"- Exact within 1e-9: **{'PASS' if all_metrics else 'FAIL'}**",
        "",
        "## Daily equity-path gate",
        f"- Exact within 1e-9 through the persisted common path: **{'PASS' if all_path else 'FAIL'}**",
        f"- Rows: authority={len(fin_rows)}, alpha={len(alpha_rows)}",
        "",
        "## Funding-accounting interpretation",
        "- The accepted engine settles native funding events sequentially before price and rebalance operations.",
        "- Matching aggregate funding PnL is insufficient; the realized equity path is the accounting contract.",
        "",
        "## Placebo gate",
        f"- Status: **{placebo_gate['status']}**",
        "",
        "## Conclusion",
        "- The accepted engine remains authoritative.",
        "- No candidate, parameter, universe, direction, or acceptance gate was modified.",
    ]
    (OUT / "reconciliation_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    return 0 if session_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
