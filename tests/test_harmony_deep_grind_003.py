from pathlib import Path

from experiments.run_harmony_reconciliation_002 import TOL
from experiments.run_harmony_deep_grind_003 import scan_integrity


def test_integrity_scan_rejects_holdout_or_search_flags():
    bad = {
        "holdout_access": False,
        "nested": {"parameter_search": True},
    }
    violations = scan_integrity(bad)
    assert any("parameter_search" in x for x in violations)


def test_v4_uses_authoritative_sharpe_layer():
    text = Path("experiments/run_alpha_autopsy_v4.py").read_text(encoding="utf-8")
    assert "rr = point_returns(curve)[1:]" in text
    assert "FIN-0012 NET RECONCILIATION GATE FAILED" in text


def test_v4_persists_daily_path_and_placebo_trials():
    text = Path("experiments/run_alpha_autopsy_v4.py").read_text(encoding="utf-8")
    assert "fin0012_daily_path.csv" in text
    assert "_persisted_placebo_trials" in text


def test_reconciliation_002_is_exact_path_gate():
    text = Path("experiments/run_harmony_reconciliation_002.py").read_text(encoding="utf-8")
    assert "equity_path_mismatch" in text
    assert "within_1e-9" in text
    assert "implementation/accounting artifact resolved" in text
    assert TOL == 1e-9
