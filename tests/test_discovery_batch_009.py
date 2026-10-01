from pathlib import Path

def test_batch_009_frozen():
    c=Path("research/HARMONY-DISCOVERY-BATCH-009.yaml").read_text()
    assert "HARMONY-FIN-0051" in c and "HARMONY-FIN-0052" in c
    assert "deep_capacity: 2" in c
    assert "no_parameter_search: true" in c
    assert "holdout_access_forbidden: true" in c
