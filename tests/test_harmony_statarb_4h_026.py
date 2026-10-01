from pathlib import Path
def test_statarb_contract():
    x=Path("research/HARMONY-STATARB-4H-026.yaml").read_text()
    for t in ["SA01","SA05","Engle-Granger coint","Z-score","cointegration",
              "objective_exclusions","no_parameter_search: true","holdout_access_forbidden: true"]:
        assert t in x
