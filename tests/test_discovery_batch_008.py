from pathlib import Path

def test_batch_008_is_frozen_and_bounded():
    cfg=Path("research/HARMONY-DISCOVERY-BATCH-008.yaml").read_text()
    for cid in ["HARMONY-FIN-0040","HARMONY-FIN-0044","HARMONY-FIN-0048","HARMONY-FIN-0049","HARMONY-FIN-0050"]:
        assert cid in cfg
    assert "minimum_rebalances: 20" in cfg
    assert "deep_capacity: 3" in cfg
    assert "posthoc_parameter_search: false" in cfg
    assert "holdout_access: false" in cfg

def test_candidate_contract_files_exist():
    for cid in ["0044","0048","0049","0050"]:
        assert Path(f"experiments/HARMONY-FIN-{cid}.yaml").is_file()
