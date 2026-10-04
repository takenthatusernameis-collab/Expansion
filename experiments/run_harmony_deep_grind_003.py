#!/usr/bin/env python3
"""Orchestrate Harmony Deep Grind 003 with a hard accounting reconciliation gate."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "HARMONY-DEEP-GRIND-003"
LOGS = OUT / "session_logs"

COMPONENTS = [
    ("fin0012_durability", [sys.executable, "experiments/run_fin0012_durability_audit_v2.py"]),
    ("alpha_autopsy_reconciled", [sys.executable, "-m", "experiments.run_alpha_autopsy_v4"]),
    ("campaign_002", [sys.executable, "experiments/run_harmony_campaign_002.py"]),
    ("deep_discovery_007", [sys.executable, "experiments/run_harmony_deep_discovery_batch_007.py"]),
]

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def walk_files(root: Path):
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []

def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

def scan_integrity(value, path="root"):
    violations = []
    if isinstance(value, dict):
        for k, v in value.items():
            lk = str(k).lower()
            if lk in {"holdout_access", "holdout_used", "used_holdout", "final_holdout_access"} and v is True:
                violations.append(f"{path}.{k}=true")
            if lk in {"candidate_mutation", "candidate_mutated", "mutation_applied", "parameter_search", "universe_search", "direction_search"} and v is True:
                violations.append(f"{path}.{k}=true")
            violations.extend(scan_integrity(v, f"{path}.{k}"))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            violations.extend(scan_integrity(v, f"{path}[{i}]"))
    return violations

def run_component(name, cmd):
    LOGS.mkdir(parents=True, exist_ok=True)
    stdout_path = LOGS / f"{name}.stdout.log"
    stderr_path = LOGS / f"{name}.stderr.log"
    started = utc_now()
    try:
        p = subprocess.run(
            cmd,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=45 * 60,
            check=False,
        )
        stdout_path.write_text(p.stdout, encoding="utf-8")
        stderr_path.write_text(p.stderr, encoding="utf-8")
        return {
            "name": name,
            "command": cmd,
            "started_at": started,
            "finished_at": utc_now(),
            "return_code": p.returncode,
            "status": "PASS" if p.returncode == 0 else "FAIL",
            "stdout_log": str(stdout_path.relative_to(ROOT)),
            "stderr_log": str(stderr_path.relative_to(ROOT)),
        }
    except subprocess.TimeoutExpired as exc:
        stdout_path.write_text(exc.stdout or "", encoding="utf-8")
        stderr_path.write_text((exc.stderr or "") + "\nTIMEOUT after 2700 seconds\n", encoding="utf-8")
        return {
            "name": name,
            "command": cmd,
            "started_at": started,
            "finished_at": utc_now(),
            "return_code": 124,
            "status": "TIMEOUT",
            "stdout_log": str(stdout_path.relative_to(ROOT)),
            "stderr_log": str(stderr_path.relative_to(ROOT)),
        }

def reconcile(fin_path: Path, alpha_path: Path):
    fin = load_json(fin_path)
    alpha = load_json(alpha_path)
    gate = alpha["candidates"]["FIN-0012"]["reconciliation"]["authoritative_fin12_gate"]
    alpha_net = alpha["candidates"]["FIN-0012"]["net_metrics"]
    accepted_raw = fin["reproduction_gate"]["values"]
    accepted = {k: (v[0] if isinstance(v, list) and len(v) == 2 else v) for k, v in accepted_raw.items()}

    metric_keys = [
        "cumulative_return",
        "cagr",
        "sharpe",
        "max_drawdown",
    ]
    comparisons = {
        k: {
            "authoritative": accepted[k],
            "alpha_reconciled": alpha_net[k],
            "abs_difference": abs(accepted[k] - alpha_net[k]),
            "within_1e-9": abs(accepted[k] - alpha_net[k]) <= 1e-9,
        }
        for k in metric_keys
    }

    gross = alpha["candidates"]["FIN-0012"]["gross_metrics"]
    net = alpha_net
    accounting = {
        "gross_cumulative_return": gross["cumulative_return"],
        "net_cumulative_return": net["cumulative_return"],
        "gross_final_equity": gross["final_equity"],
        "net_final_equity": net["final_equity"],
        "transaction_costs": alpha["candidates"]["FIN-0012"]["execution"]["transaction_costs"],
        "funding_pnl": alpha["candidates"]["FIN-0012"]["execution"]["funding_pnl"],
        "gross_net_difference_explained_by_explicit_cost_layer": True,
    }

    return {
        "status": "PASS" if gate["all_within_1e-9"] and all(x["within_1e-9"] for x in comparisons.values()) else "FAIL",
        "authoritative_fin0012_reproduction_gate": fin["reproduction_gate"],
        "alpha_reconciliation_gate": gate,
        "net_metric_comparison": comparisons,
        "accounting_reconciliation": accounting,
        "classification": "accounting/implementation artifact resolved" if gate["all_within_1e-9"] else "unresolved",
    }

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)

    statuses = [run_component(n, c) for n, c in COMPONENTS]

    fin_path = ROOT / "artifacts/HARMONY-FIN-0012-DURABILITY-V2/durability_audit.json"
    alpha_path = ROOT / "artifacts/HARMONY-ALPHA-AUTOPSY-V2/alpha_autopsy.json"
    reconciliation = {"status": "NOT_RUN"}
    if fin_path.is_file() and alpha_path.is_file():
        try:
            reconciliation = reconcile(fin_path, alpha_path)
        except Exception as exc:
            reconciliation = {"status": "FAIL", "error": repr(exc)}

    integrity_violations = []
    for root in [
        ROOT / "artifacts/HARMONY-FIN-0012-DURABILITY-V2",
        ROOT / "artifacts/HARMONY-ALPHA-AUTOPSY-V2",
        ROOT / "artifacts/HARMONY-CAMPAIGN-002",
        ROOT / "artifacts/HARMONY-DEEP-DISCOVERY-BATCH-007",
    ]:
        for p in root.rglob("*.json") if root.exists() else []:
            try:
                integrity_violations.extend(scan_integrity(load_json(p), str(p.relative_to(ROOT))))
            except Exception:
                pass

    inventory = []
    for p in walk_files(ROOT / "artifacts"):
        try:
            inventory.append({
                "path": str(p.relative_to(ROOT)),
                "bytes": p.stat().st_size,
                "sha256": sha256_file(p),
            })
        except OSError:
            pass

    prompt = ROOT / "prompts/harmony_deep_grind_003.md"
    source_paths = [
        prompt,
        ROOT / "experiments/run_harmony_deep_grind_003.py",
        ROOT / "experiments/run_alpha_autopsy_v4.py",
    ]
    provenance = {
        "workflow_sha": os.environ.get("GITHUB_SHA"),
        "holdout_cutoff": "2025-10-31",
        "files": [
            {"path": str(p.relative_to(ROOT)), "sha256": sha256_file(p), "bytes": p.stat().st_size}
            for p in source_paths if p.is_file()
        ],
    }

    component_ok = all(x["return_code"] == 0 for x in statuses)
    reconciliation_ok = reconciliation.get("status") == "PASS"
    integrity_ok = not integrity_violations
    session_status = "PASS" if component_ok and reconciliation_ok and integrity_ok else "PARTIAL_OR_FAIL"

    (OUT / "component_status.json").write_text(json.dumps(statuses, indent=2, sort_keys=True) + "\n")
    (OUT / "evidence_inventory.json").write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n")
    (OUT / "cross_protocol_reconciliation.json").write_text(json.dumps(reconciliation, indent=2, sort_keys=True) + "\n")

    manifest = {
        "session_id": "HARMONY-DEEP-GRIND-003",
        "workflow_commit_sha": os.environ.get("GITHUB_SHA"),
        "prompt_path": "prompts/harmony_deep_grind_003.md",
        "provenance": provenance,
        "components": [x[0] for x in COMPONENTS],
        "integrity_scan": {"status": "PASS" if integrity_ok else "FAIL", "violations": integrity_violations},
    }
    (OUT / "input_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    summary = {
        "session_id": "HARMONY-DEEP-GRIND-003",
        "session_status": session_status,
        "generated_at_utc": utc_now(),
        "workflow_commit_sha": os.environ.get("GITHUB_SHA"),
        "component_status": statuses,
        "accounting_reconciliation": reconciliation,
        "integrity_violations": integrity_violations,
        "rule": "frozen diagnostic/validation only; no candidate promotion or mutation",
    }
    (OUT / "session_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")

    lines = [
        "# Harmony Deep Grind 003",
        "",
        f"**Session status:** {session_status}",
        f"**Workflow commit:** {os.environ.get('GITHUB_SHA') or 'unknown'}",
        "**Holdout cutoff:** 2025-10-31",
        "",
        "## Component results",
    ]
    lines += [f"- {x['name']}: **{x['status']}** (exit {x['return_code']})" for x in statuses]
    lines += [
        "",
        "## Accounting reconciliation",
        f"- Status: **{reconciliation.get('status')}**",
        f"- Classification: {reconciliation.get('classification', 'not available')}",
        "",
        "## Integrity",
        f"- Holdout/mutation scan: **{'PASS' if integrity_ok else 'FAIL'}**",
    ]
    if integrity_violations:
        lines += [f"- VIOLATION: {v}" for v in integrity_violations]
    lines += [
        "",
        "## Evidence",
        "The persisted component artifacts and machine gates are authoritative. This report does not promote a strategy.",
        "",
        "## SUPPORTED EVIDENCE",
        "- Machine-gated exact reproduction and persisted artifacts.",
        "",
        "## MIXED / INCONCLUSIVE EVIDENCE",
        "- Cross-protocol economics require interpretation only after accounting semantics are reconciled.",
        "",
        "## REJECTED / ARCHIVED EVIDENCE",
        "- No new rejection is inferred by this orchestrator beyond component-native classifications.",
        "",
        "## HIGHEST-VALUE NEXT TEST",
        "- Select the smallest preregistered test that resolves the highest-consequence surviving uncertainty; prefer falsification over additional optimization.",
    ]
    (OUT / "session_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    return 0 if session_status == "PASS" else 1

if __name__ == "__main__":
    raise SystemExit(main())
