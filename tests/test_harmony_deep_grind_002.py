from pathlib import Path

from experiments.run_harmony_deep_grind_002 import scan_integrity
from experiments.run_harmony_reconciliation_001 import close


def test_integrity_scan_rejects_search_and_holdout_flags():
    bad = {
        "holdout_access": False,
        "nested": {
            "parameter_search": True,
            "candidate_mutation": False,
        },
    }
    violations = scan_integrity(bad)
    assert any("parameter_search" in x for x in violations)


def test_reconciliation_tolerance():
    assert close(1.0, 1.0 + 5e-10)
    assert not close(1.0, 1.0 + 2e-9)


def test_alpha_autopsy_declares_accounting_layers():
    text = Path("experiments/run_alpha_autopsy_v1.py").read_text(encoding="utf-8")
    assert "gross_return_series_before_transaction_costs" in text
    assert "realized_equity_after_transaction_costs_and_funding" in text
    assert "realized_equity_net_of_costs_and_funding" in text


def test_deep_grind_has_reconciliation_dependency():
    text = Path("experiments/run_harmony_deep_grind_002.py").read_text(encoding="utf-8")
    assert "fin0012_reconciliation" in text
    assert "reconciliation_status" in text
