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
    assert g.MARKETS[0]=="binance-BTCUSDT-future"
