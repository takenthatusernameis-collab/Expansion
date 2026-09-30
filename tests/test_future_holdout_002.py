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


def test_candidate_scope_digest_is_bound():
    text = SCRIPT.read_text()
    assert "CANDIDATE_SCOPE_DIGEST" in text
    assert "candidate_scope_digest" in text
    assert "\"candidate_ids\":CAND" in text


def test_workflow_cache_key_uses_runtime_run_id():
    workflow = (SCRIPT.parents[1] / ".github/workflows/harmony-future-holdout-002.yml").read_text()
    assert "key: harmony-future-holdout-2026-09-forward-v1-${{ github.run_id }}" in workflow
    assert "\\${{ github.run_id }}" not in workflow


def test_gate_requires_contiguous_complete_start():
    text = SCRIPT.read_text()
    assert "WAITING_FOR_180_CONTIGUOUS_COMPLETE_OBSERVATIONS" in text
    assert "d==START" in text
    assert "all(funding[s].get(d) for s in SYMBOLS)" in text
    assert "(d-contiguous[-1]).days==1" in text
