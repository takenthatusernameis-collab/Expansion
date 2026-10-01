from pathlib import Path
def test_tech_027_contract():
    x=Path("research/HARMONY-TECH-4H-027.yaml").read_text()
    for t in ["TC01","TC10","JTOUSDT","JUPUSDT","ta==0.11.0","no_parameter_search: true"]:
        assert t in x
