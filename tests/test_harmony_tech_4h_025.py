from pathlib import Path
def test_tech_4h_contract():
    x=Path("research/HARMONY-TECH-4H-025.yaml").read_text()
    for t in ["TC01","TC10","ta==0.11.0","trend_reversal_resumption",
              "no_parameter_search: true","no_universe_search: true","holdout_access_forbidden: true"]:
        assert t in x
