import json
from experiments.run_harmony_deep_grind_001 import metric_highlights, scan_integrity


def test_integrity_scan_rejects_holdout_and_mutation():
    bad = {
        "holdout_access": False,
        "candidate_mutation": False,
        "nested": {"parameter_search": True},
    }
    violations = scan_integrity(bad)
    assert any("parameter_search" in x for x in violations)


def test_metric_highlights_extracts_core_metrics():
    value = {"raw_metrics": {"sharpe": 1.2, "cagr": 0.3, "max_drawdown": -0.2}}
    rows = metric_highlights(value)
    keys = {row["path"] for row in rows}
    assert "root.raw_metrics.sharpe" in keys
    assert "root.raw_metrics.cagr" in keys
    assert "root.raw_metrics.max_drawdown" in keys
