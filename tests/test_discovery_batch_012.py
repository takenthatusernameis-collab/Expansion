from pathlib import Path

def test_batch_012_frozen():
    c=Path("research/HARMONY-DISCOVERY-BATCH-012.yaml").read_text()
    for cid in ["HARMONY-FIN-0062","HARMONY-FIN-0063","HARMONY-FIN-0064","HARMONY-FIN-0065"]:
        assert cid in c
    assert "deep_capacity: 2" in c
    assert "no_parameter_search: true" in c
    assert "holdout_access_forbidden: true" in c
