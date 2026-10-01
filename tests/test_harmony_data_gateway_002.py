from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import data_sources.harmony_data_gateway_002 as g

def test_fixed_assets():
    assert g.ASSETS == ["btc","eth","ltc","xrp","bnb","bch","ada","doge"]
    assert g.SYMBOLS == ["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]

def test_frozen_period():
    assert g.START=="2020-01-01"
    assert g.END=="2025-10-31"
    assert g.CM_COMMIT=="f1a36afb962731c387bb03982758ab0103063da5"
