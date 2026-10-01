from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import experiments.run_harmony_discovery_batch_016 as r

def test_fixed_contract():
    assert r.S == ["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
    assert r.DISCOVERY_END=="2024-05-21"
    assert r.OOS_START=="2024-05-22"

def test_fixed_candidates():
    assert ["FIN-0085","FIN-0086","FIN-0087","FIN-0088"] == ["FIN-0085","FIN-0086","FIN-0087","FIN-0088"]
