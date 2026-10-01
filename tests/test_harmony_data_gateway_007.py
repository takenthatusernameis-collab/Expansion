from pathlib import Path
def test_gateway_007_contract():
    x=Path("research/HARMONY-DATA-GATEWAY-007.yaml").read_text()
    for t in ["LMNUF12M","beta_window_months: 24","vintage_proof_required_before_guarded_promotion: true"]:
        assert t in x
