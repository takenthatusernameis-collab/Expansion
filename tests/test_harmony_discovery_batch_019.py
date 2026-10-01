from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import experiments.run_harmony_discovery_batch_019 as r

def test_fixed_contract():
    assert r.S == ["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
    assert r.DISCOVERY_END=="2024-05-21"
    assert r.OOS_START=="2024-05-22"
    assert r.END=="2025-10-31"
    w=r.rank_weights([(0.1,"BTCUSDT"),(0.2,"ETHUSDT"),(0.3,"LTCUSDT"),(0.4,"XRPUSDT"),
                      (0.5,"BNBUSDT"),(0.6,"BCHUSDT"),(0.7,"ADAUSDT"),(0.8,"DOGEUSDT")])
    assert sum(v>0 for v in w.values())==3
    assert sum(v<0 for v in w.values())==3
    assert abs(sum(abs(v) for v in w.values())-1.0)<1e-12

def test_fixed_12_week_shape():
    dates=[f"2024-01-{d:02d}" for d in range(1,15)]
    supply={a:{d:100.0 for d in dates} for a in r.A}
    # Force a deterministic positive weekly growth segment.
    supply["btc"]["2024-01-14"]=101.0
    out=r.score("2024-02-05",dates,supply)
    assert out is None
