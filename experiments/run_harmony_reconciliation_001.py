#!/usr/bin/env python3
"""Reconcile FIN-0012 alpha-autopsy diagnostics against the authoritative durability trace."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTH = ROOT / "artifacts" / "HARMONY-FIN-0012-DURABILITY-V2" / "durability_audit.json"
ALPHA = ROOT / "artifacts" / "HARMONY-ALPHA-AUTOPSY-V1" / "alpha_autopsy.json"
PLACEBO = ROOT / "artifacts" / "HARMONY-ALPHA-AUTOPSY-V1" / "placebo_summary.json"
OUT = ROOT / "artifacts" / "HARMONY-RECONCILIATION-001"
TOL = 1e-9

METRICS = ("cumulative_return", "cagr", "sharpe", "max_drawdown", "final_equity", "observations")
EXECUTION = ("turnover", "transaction_costs", "funding_pnl")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def percentile(actual, values):
    return 100.0 * sum(v <= actual for v in values) / len(values)


def close(a, b, tol=TOL):
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    return abs(float(a) - float(b)) <= tol


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    required = [AUTH, ALPHA, PLACEBO]
    missing = [str(p.relative_to(ROOT)) for p in required if not p.is_file()]
    if missing:
        payload = {"status": "FAIL", "reason": "missing_required_artifacts", "missing": missing}
        (OUT / "reconciliation.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        (OUT / "reconciliation_report.md").write_text("# FIN-0012 Reconciliation 001\n\nFAIL: required artifacts missing.\n")
        return 1

    auth = json.loads(AUTH.read_text(encoding="utf-8"))
    alpha = json.loads(ALPHA.read_text(encoding="utf-8"))
    placebo = json.loads(PLACEBO.read_text(encoding="utf-8"))

    auth_metrics = auth.get("trace_metrics") or {}
    alpha_fin12 = (alpha.get("candidates") or {}).get("FIN-0012") or {}
    alpha_net = alpha_fin12.get("net_metrics") or {}
    alpha_raw = alpha_fin12.get("raw_metrics") or {}
    alpha_exec = alpha_fin12.get("execution") or {}
    auth_exec = auth.get("reproduction_gate") or {}

    metric_diffs = {}
    for k in METRICS:
        if k not in auth_metrics or k not in alpha_net:
            metric_diffs[k] = {"error": "missing"}
        elif not close(auth_metrics[k], alpha_net[k]):
            metric_diffs[k] = {"authoritative": auth_metrics[k], "alpha_net": alpha_net[k]}

    execution_diffs = {}
    for k in EXECUTION:
        if k not in auth_exec or k not in alpha_exec:
            execution_diffs[k] = {"error": "missing"}
        elif not close(auth_exec[k], alpha_exec[k]):
            execution_diffs[k] = {"authoritative": auth_exec[k], "alpha": alpha_exec[k]}

    layer_map = alpha_fin12.get("accounting_layers") or {}
    layer_ok = (
        layer_map.get("raw_metrics") == "gross_return_series_before_transaction_costs"
        and layer_map.get("net_metrics") == "realized_equity_after_transaction_costs_and_funding"
        and layer_map.get("placebo_percentiles") == "realized_equity_net_of_costs_and_funding"
        and layer_map.get("diagnostic_only") is True
    )

    p = (alpha_fin12.get("placebo") or {})
    trials = (placebo.get("FIN-0012") or [])
    placebo_checks = {}
    if not trials:
        placebo_checks["error"] = {"pass": False, "reason": "missing_FIN0012_trials"}
    else:
        for metric_name, actual in (
            ("actual_cumulative_percentile", alpha_net.get("cumulative_return")),
            ("actual_sharpe_percentile", alpha_net.get("sharpe")),
            ("actual_mdd_percentile", alpha_net.get("max_drawdown")),
        ):
            key = {
                "actual_cumulative_percentile": "cum",
                "actual_sharpe_percentile": "sharpe",
                "actual_mdd_percentile": "mdd",
            }[metric_name]
            expected = percentile(actual, [float(x[key]) for x in trials])
            observed = p.get(metric_name)
            placebo_checks[metric_name] = {
                "observed": observed,
                "expected_from_recorded_trials": expected,
                "pass": observed is not None and abs(float(observed) - expected) <= 1e-12,
            }

    gross_net_gap = {
        k: {
            "gross": alpha_raw.get(k),
            "net": alpha_net.get(k),
            "difference": (alpha_raw.get(k) - alpha_net.get(k))
            if isinstance(alpha_raw.get(k), (int, float)) and isinstance(alpha_net.get(k), (int, float))
            else None,
        }
        for k in ("cumulative_return", "cagr", "sharpe", "max_drawdown")
    }

    all_pass = not missing and not metric_diffs and not execution_diffs and layer_ok and all(
        x.get("pass") is True for x in placebo_checks.values() if isinstance(x, dict)
    )

    payload = {
        "reconciliation_id": "HARMONY-RECONCILIATION-001",
        "status": "PASS" if all_pass else "FAIL",
        "authority": {
            "path": str(AUTH.relative_to(ROOT)),
            "sha256": sha256_file(AUTH),
            "engine_source_commit": auth.get("engine_source_commit"),
        },
        "alpha_autopsy": {
            "path": str(ALPHA.relative_to(ROOT)),
            "sha256": sha256_file(ALPHA),
        },
        "placebo_summary": {
            "path": str(PLACEBO.relative_to(ROOT)),
            "sha256": sha256_file(PLACEBO),
        },
        "metric_diffs": metric_diffs,
        "execution_diffs": execution_diffs,
        "accounting_layer_gate": layer_ok,
        "placebo_checks": placebo_checks,
        "gross_vs_net_gap": gross_net_gap,
        "interpretation": (
            "The authoritative engine remains the source of truth. "
            "Any gross/net gap is an accounting-layer diagnostic, not evidence of a different signal. "
            "No candidate was changed to force agreement."
        ),
        "integrity": {
            "holdout_access": False,
            "candidate_mutation": False,
            "parameter_search": False,
            "universe_search": False,
        },
    }

    (OUT / "reconciliation.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    lines = [
        "# FIN-0012 Reconciliation 001",
        "",
        f"Status: {payload['status']}",
        "",
        "## Authority",
        f"Durability audit SHA-256: {payload['authority']['sha256']}",
        "",
        "## Metric gate",
        "- PASS: alpha-autopsy net metrics reproduce the authoritative trace within 1e-9."
        if not metric_diffs else "- FAIL: net metric mismatch detected.",
        "",
        "## Execution gate",
        "- PASS: turnover, transaction costs, and funding PnL agree within 1e-9."
        if not execution_diffs else "- FAIL: execution-accounting mismatch detected.",
        "",
        "## Accounting-layer gate",
        f"- {'PASS' if layer_ok else 'FAIL'}: gross and net diagnostics are explicitly labelled.",
        "",
        "## Placebo gate",
    ]
    for k, v in placebo_checks.items():
        lines.append(f"- {k}: {'PASS' if v.get('pass') else 'FAIL'}")
    lines += [
        "",
        "## Interpretation",
        "- The accepted FIN-0012 durability engine is authoritative.",
        "- A gross-vs-net difference is not itself a signal discrepancy.",
        "- This artifact does not promote, reject, or retune FIN-0012.",
    ]
    (OUT / "reconciliation_report.md").write_text("\n".join(lines) + "\n")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
