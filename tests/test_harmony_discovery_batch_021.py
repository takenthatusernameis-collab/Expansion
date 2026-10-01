from pathlib import Path
def test_batch_021_contract():
    x=Path("research/HARMONY-DISCOVERY-BATCH-021.yaml").read_text()
    for token in ["FIN-0103","FIN-0104","FIN-0105","FIN-0106","FIN-0107",
                  "63-day","no_parameter_search: true","no_universe_search: true",
                  "no_direction_search: true","no_candidate_mutation: true","holdout_access_forbidden: true"]:
        assert token in x
