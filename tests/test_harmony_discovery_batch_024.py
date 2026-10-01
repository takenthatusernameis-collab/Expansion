from pathlib import Path
def test_batch_024_contract():
    x=Path("research/HARMONY-DISCOVERY-BATCH-024.yaml").read_text()
    for t in ["FIN-0121","FIN-0122","FIN-0123","FIN-0124","FIN-0125","FIN-0126",
              "liquidity_commonality_beta","illiquidity_momentum_interaction",
              "no_parameter_search: true","no_universe_search: true","no_direction_search: true",
              "no_candidate_mutation: true","holdout_access_forbidden: true"]:
        assert t in x
