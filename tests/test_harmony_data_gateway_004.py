from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import data_sources.harmony_data_gateway_004 as g

def test_fixed_spot_contract():
    assert g.SYMBOLS == ["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
    assert (g.START_YEAR,g.START_MONTH)==(2020,7)
    assert (g.END_YEAR,g.END_MONTH)==(2025,10)
