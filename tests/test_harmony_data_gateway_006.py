from pathlib import Path
def test_gateway_006_contract():
    x=Path("research/HARMONY-DATA-GATEWAY-006.yaml").read_text()
    for t in ["data.binance.vision","4h","flow_imbalance_21d","flow_z_21d","normalized_feature_sha256: required"]:
        assert t in x
