from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "experiments" / "run_harmony_future_holdout_002.py"


def test_candidate_dispatch_checks_exact_ids():
    text = SCRIPT.read_text()
    assert 'cid=="funding_carry_zscore_7d_weekly_v1"' in text
    assert 'elif cid=="funding_carry_mean_7d_weekly_v1"' in text
    assert 'endswith("7d_weekly_v1")' not in text


def test_preregistered_benchmarks_are_present():
    text = SCRIPT.read_text()
    assert 'same_universe_equal_weight_long_only' in text
    assert 'BTCUSDT_buy_and_hold' in text
