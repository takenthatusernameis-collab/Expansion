from pathlib import Path

def test_batch_020_contract():
    p=Path("research/HARMONY-DISCOVERY-BATCH-020.yaml").read_text()
    for token in [
        "FIN-0097","FIN-0098","FIN-0099","FIN-0100","FIN-0101","FIN-0102",
        "feature_interval: 4h","no_parameter_search: true","no_universe_search: true",
        "no_direction_search: true","no_candidate_mutation: true","holdout_access_forbidden: true"
    ]:
        assert token in p
