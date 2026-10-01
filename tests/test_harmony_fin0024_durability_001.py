from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import experiments.run_harmony_fin0024_durability_001 as r

def test_frozen_baseline_constants():
    assert r.EXPECTED["cumulative_return"] == 0.43002307869081013
    assert r.EXPECTED["sharpe"] == 1.3589824243083648
    assert r.COST_MULTIPLIERS == (1.0, 2.0, 3.0, 4.0, 5.0)

def test_fixed_universe():
    assert r.SYMBOLS == ["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
