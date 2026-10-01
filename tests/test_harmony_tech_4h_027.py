from pathlib import Path
def test_tech_027_contract():
    x=Path("research/HARMONY-TECH-4H-027.yaml").read_text()
    for t in ["TC01","TC10","JTOUSDT","JUPUSDT","ta==0.11.0","no_parameter_search: true"]:
        assert t in x


def test_pullback_logic_contract_text():
    code = __import__("pathlib").Path("experiments/run_harmony_tech_4h_027.py").read_text()
    assert 'float(df["close"].iloc[i-1])<float(df["close"].iloc[i-2])' in code
    assert 'float(df["close"].iloc[i-1])>float(df["close"].iloc[i-2])' in code
    assert 'float(df["close"].iloc[i])<=float(df["close"].iloc[i-1])' not in code
    assert 'float(df["close"].iloc[i])>=float(df["close"].iloc[i-1])' not in code


def test_portfolio_accounting_repair_contract():
    code = Path("experiments/run_harmony_tech_4h_027.py").read_text()
    assert "position_weight=portfolio_weight(len(assets_data))" in code
    assert "realized_pnl_weight += pos["weight"] * ret" in code
    assert "turnover_weight += pos["weight"]" in code
    assert "ret/max(1,len(active))" not in code
    assert "equity*=max(0.0, 1.0 - cost * turnover_weight)" in code
