from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import data_sources.harmony_data_gateway_003 as g
def test_fixed_snapshot():
    assert g.CM_COMMIT=="f1a36afb962731c387bb03982758ab0103063da5"
    assert g.ASSETS==["btc","eth","ltc","xrp","bnb","bch","ada","doge"]
