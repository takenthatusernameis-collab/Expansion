from pathlib import Path
def test_batch_022_contract():
    x=Path("research/HARMONY-DISCOVERY-BATCH-022.yaml").read_text()
    for t in ["FIN-0109","FIN-0110","FIN-0111","FIN-0112","FIN-0113","FIN-0114","4h",
              "no_parameter_search: true","no_universe_search: true","no_direction_search: true",
              "no_candidate_mutation: true","holdout_access_forbidden: true"]:
        assert t in x
