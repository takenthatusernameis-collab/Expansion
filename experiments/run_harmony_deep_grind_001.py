#!/usr/bin/env python3
"""Orchestrate a bounded, research-integrity-first Harmony deep-grind session."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts' / 'HARMONY-DEEP-GRIND-001'
LOGS = OUT / 'session_logs'

COMPONENTS = [
    ('fin0012_durability', [sys.executable, 'experiments/run_fin0012_durability_audit_v2.py']),
    ('alpha_autopsy', [sys.executable, 'experiments/run_alpha_autopsy_v1.py']),
    ('campaign_002', [sys.executable, 'experiments/run_harmony_campaign_002.py']),
    ('deep_discovery_007', [sys.executable, 'experiments/run_harmony_deep_discovery_batch_007.py']),
]

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

def walk_files(root: Path):
    if not root.exists():
        return []
    return sorted(p for p in root.rglob('*') if p.is_file())

def scan_integrity(value, path='root') -> list[str]:
    violations = []
    if isinstance(value, dict):
        for k, v in value.items():
            key = str(k).lower()
            if key in {'holdout_access', 'holdout_used', 'used_holdout', 'final_holdout_access'} and v is True:
                violations.append(f'{path}.{k}=true')
            if key in {'candidate_mutation', 'candidate_mutated', 'mutation_applied', 'parameter_search', 'universe_search', 'direction_search'} and v is True:
                violations.append(f'{path}.{k}=true')
            violations.extend(scan_integrity(v, f'{path}.{k}'))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            violations.extend(scan_integrity(v, f'{path}[{i}]'))
    return violations

def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None

def metric_highlights(value, prefix='root') -> list[dict]:
    out = []
    if isinstance(value, dict):
        for k, v in value.items():
            lk = str(k).lower()
            if lk in {'sharpe', 'cagr', 'cumulative_return', 'max_drawdown', 'sortino', 'actual_sharpe_percentile', 'actual_cumulative_percentile'} and isinstance(v, (int, float)):
                out.append({'path': f'{prefix}.{k}', 'value': v})
            out.extend(metric_highlights(v, f'{prefix}.{k}'))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out.extend(metric_highlights(v, f'{prefix}[{i}]'))
    return out

def run_component(name: str, cmd: list[str]) -> dict:
    LOGS.mkdir(parents=True, exist_ok=True)
    stdout_path = LOGS / f'{name}.stdout.log'
    stderr_path = LOGS / f'{name}.stderr.log'
    started = utc_now()
    try:
        proc = subprocess.run(
            cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=45 * 60, check=False
        )
        stdout_path.write_text(proc.stdout, encoding='utf-8')
        stderr_path.write_text(proc.stderr, encoding='utf-8')
        return {
            'name': name, 'command': cmd, 'started_at': started, 'finished_at': utc_now(),
            'return_code': proc.returncode, 'status': 'PASS' if proc.returncode == 0 else 'FAIL',
            'stdout_log': str(stdout_path.relative_to(ROOT)), 'stderr_log': str(stderr_path.relative_to(ROOT)),
        }
    except subprocess.TimeoutExpired as exc:
        stdout_path.write_text(exc.stdout or '', encoding='utf-8')
        stderr_path.write_text((exc.stderr or '') + '\nTIMEOUT after 2700 seconds\n', encoding='utf-8')
        return {
            'name': name, 'command': cmd, 'started_at': started, 'finished_at': utc_now(),
            'return_code': 124, 'status': 'TIMEOUT',
            'stdout_log': str(stdout_path.relative_to(ROOT)), 'stderr_log': str(stderr_path.relative_to(ROOT)),
        }

def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    statuses = []
    for name, cmd in COMPONENTS:
        statuses.append(run_component(name, cmd))

    evidence = []
    integrity_violations = []
    highlights = []
    candidate_outputs = [
        ROOT / 'artifacts' / 'HARMONY-FIN-0012-DURABILITY-V2' / 'durability_audit.json',
        ROOT / 'artifacts' / 'HARMONY-ALPHA-AUTOPSY-V1' / 'alpha_autopsy.json',
        ROOT / 'artifacts' / 'HARMONY-CAMPAIGN-002' / 'summary.json',
        ROOT / 'artifacts' / 'HARMONY-DEEP-DISCOVERY-BATCH-007' / 'HARMONY-DEEP-DISCOVERY-BATCH-007-RESULT.json',
    ]
    for p in candidate_outputs:
        if p.is_file():
            evidence.append({
                'path': str(p.relative_to(ROOT)),
                'bytes': p.stat().st_size,
                'sha256': sha256_file(p),
            })
            obj = load_json(p)
            if obj is not None:
                integrity_violations.extend(scan_integrity(obj, str(p.relative_to(ROOT))))
                highlights.extend(metric_highlights(obj, str(p.relative_to(ROOT))))

    inventory = []
    for p in walk_files(ROOT / 'artifacts'):
        try:
            inventory.append({'path': str(p.relative_to(ROOT)), 'bytes': p.stat().st_size, 'sha256': sha256_file(p)})
        except OSError:
            pass

    git_sha = os.environ.get('GITHUB_SHA')
    component_ok = all(x['return_code'] == 0 for x in statuses)
    integrity_ok = not integrity_violations
    session_status = 'PASS' if component_ok and integrity_ok else 'PARTIAL_OR_FAIL'

    session_summary = {
        'session_id': 'HARMONY-DEEP-GRIND-001',
        'session_status': session_status,
        'generated_at_utc': utc_now(),
        'workflow_commit_sha': git_sha,
        'holdout_cutoff': '2025-10-31',
        'component_status': statuses,
        'integrity_violations': integrity_violations,
        'research_rule': 'diagnostic and validation only; no candidate promotion or mutation',
    }
    (OUT / 'session_summary.json').write_text(json.dumps(session_summary, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    (OUT / 'component_status.json').write_text(json.dumps(statuses, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    (OUT / 'evidence_inventory.json').write_text(json.dumps(inventory, indent=2, sort_keys=True) + '\n', encoding='utf-8')

    manifest = {
        'session_id': 'HARMONY-DEEP-GRIND-001',
        'workflow_commit_sha': git_sha,
        'prompt_path': 'prompts/harmony_deep_grind_v1.md',
        'component_outputs': evidence,
        'all_artifacts': inventory,
        'integrity_scan': {'status': 'PASS' if integrity_ok else 'FAIL', 'violations': integrity_violations},
    }
    manifest_raw = json.dumps(manifest, indent=2, sort_keys=True).encode() + b'\n'
    manifest_path = OUT / 'input_manifest.json'
    manifest_path.write_bytes(manifest_raw)

    lines = [
        '# Harmony Deep Grind 001',
        '',
        f'**Session status:** {session_status}',
        f'**Workflow commit:** {git_sha or "unknown"}',
        '**Holdout cutoff:** 2025-10-31',
        '',
        '## Component results',
    ]
    for x in statuses:
        lines.append(f"- {x['name']}: **{x['status']}** (exit {x['return_code']})")
    lines += ['', '## Integrity', f'- Holdout/mutation scan: **{"PASS" if integrity_ok else "FAIL"}**']
    if integrity_violations:
        lines.extend([f'- VIOLATION: {v}' for v in integrity_violations])
    lines += ['', '## Evidence', 'The session inventory hashes every generated artifact. Component-native reports remain authoritative; this report does not reinterpret their numerical conclusions.']
    lines += ['', '## Metric highlights (descriptive only)']
    for h in highlights[:120]:
        lines.append(f"- {h['path']}: {h['value']}")
    lines += ['', '## SUPPORTED EVIDENCE', '- Component exit status and persisted artifacts are the authoritative evidence.']
    lines += ['', '## MIXED / INCONCLUSIVE EVIDENCE', '- Cross-protocol synthesis remains diagnostic until component reports are reviewed together.']
    lines += ['', '## REJECTED / ARCHIVED EVIDENCE', '- None are inferred by this orchestrator; component-native rejection/archival decisions remain authoritative.']
    lines += ['', '## HIGHEST-VALUE NEXT TEST', '- Select the smallest preregistered test that directly resolves the most consequential surviving uncertainty; do not optimize a candidate merely because this session produced an attractive metric.']
    (OUT / 'session_report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')

    return 0 if session_status == 'PASS' else 1

if __name__ == '__main__':
    raise SystemExit(main())