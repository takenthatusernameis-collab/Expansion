from pathlib import Path

SCRIPT=Path(__file__).parents[1]/"experiments/run_harmony_future_holdout_003.py"

def test_continuous_holdout_uses_elapsed_end():
    t=SCRIPT.read_text()
    assert 'END=datetime.now(timezone.utc).date()-timedelta(days=1)' in t
    assert "HARMONY-HOLDOUT-PROTOCOL-003" in t
    assert "ACCRUING_ELAPSED_DATA" in t

def test_daily_source_and_gateway_are_bound():
    t=SCRIPT.read_text()
    assert "data.binance.vision/data/futures/um/daily/klines" in t
    assert "api-dev.pipai.org/funding/rates/" in t
    assert "candidate_scope_digest" in t
