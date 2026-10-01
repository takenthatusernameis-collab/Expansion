from pathlib import Path
def test_batch_023_contract():
    x=Path("research/HARMONY-DISCOVERY-BATCH-023.yaml").read_text()
    for t in ["FIN-0115","FIN-0116","FIN-0117","FIN-0118","FIN-0119",
              "24-month","vintage/PIT","no_parameter_search: true","no_universe_search: true",
              "no_direction_search: true","no_candidate_mutation: true","holdout_access_forbidden: true"]:
        assert t in x
