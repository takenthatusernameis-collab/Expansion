from pathlib import Path
import json
def test_gateway_005_contract():
    p=Path("research/HARMONY-DATA-GATEWAY-005.yaml")
    x=p.read_text()
    for token in ["data.binance.vision","4h","current_value_backfill: false","per_asset_coverage_certificate: required","request_sha256_manifest: required"]:
        assert token in x
