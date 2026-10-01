from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import experiments.run_harmony_discovery_batch_015 as r

def test_fixed_universe_and_window():
    assert r.S == ["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
    assert r.DISCOVERY_END=="2024-05-21"
    assert r.OOS_START=="2024-05-22"

def test_fixed_candidates():
    assert ["FIN-0079","FIN-0080","FIN-0082"] == ["FIN-0079","FIN-0080","FIN-0082"]
