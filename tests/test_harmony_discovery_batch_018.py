from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import experiments.run_harmony_discovery_batch_018 as r

def test_fixed_universe_and_window():
    assert r.S == ["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
    assert r.DISCOVERY_END=="2024-05-21"
    assert r.OOS_START=="2024-05-22"
    assert r.END=="2025-10-31"

def test_fixed_fields_and_rank_direction():
    dates=[
        "2024-04-29","2024-04-30","2024-05-01","2024-05-02","2024-05-03","2024-05-04","2024-05-05",
        "2024-05-06","2024-05-07","2024-05-08","2024-05-09","2024-05-10","2024-05-11","2024-05-12"
    ]
    activity={a:{d:{"TxCnt":1.0 if i<7 else 2.0} for i,d in enumerate(dates)} for a in r.A}
    assert r.score("FIN-0091","2024-05-13",dates,activity) is not None
    w=r.rank_weights([(0.1,"BTCUSDT"),(0.2,"ETHUSDT"),(0.3,"LTCUSDT"),(0.4,"XRPUSDT"),
                      (0.5,"BNBUSDT"),(0.6,"BCHUSDT"),(0.7,"ADAUSDT"),(0.8,"DOGEUSDT")])
    assert sum(v>0 for v in w.values())==3
    assert sum(v<0 for v in w.values())==3
    assert abs(sum(abs(v) for v in w.values())-1.0)<1e-12
