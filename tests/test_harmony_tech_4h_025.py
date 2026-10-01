from pathlib import Path
def test_tech_4h_contract():
    x=Path("research/HARMONY-TECH-4H-025.yaml").read_text()
    for t in ["TC01","TC10","ta==0.11.0","trend_reversal_resumption",
              "no_parameter_search: true","no_universe_search: true","holdout_access_forbidden: true"]:
        assert t in x


def test_portfolio_accounting_repair_contract():
    code = Path("experiments/run_harmony_tech_4h_025.py").read_text()
    assert "position_weight=portfolio_weight(len(assets_data))" in code
    assert "realized_pnl_weight += pos["weight"] * ret" in code
    assert "turnover_weight += pos["weight"]" in code
    assert "ret/max(1,len(active))" not in code
    assert "equity*=max(0.0, 1.0 - cost * turnover_weight)" in code
