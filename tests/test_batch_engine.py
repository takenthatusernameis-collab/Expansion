import math

from harmony_backtest.batch_engine import FundingEvent, build_shared_state, run_weight_batch
from harmony_backtest.daily_store import DailyMarketRow


def _rows():
    return [
        DailyMarketRow("A", "2025-01-01", 100.0, 1000.0, 600.0),
        DailyMarketRow("A", "2025-01-02", 110.0, 1100.0, 700.0),
        DailyMarketRow("A", "2025-01-03", 121.0, 1200.0, 800.0),
        DailyMarketRow("B", "2025-01-01", 100.0, 1000.0, 400.0),
        DailyMarketRow("B", "2025-01-02", 90.0, 1000.0, 300.0),
        DailyMarketRow("B", "2025-01-03", 81.0, 1000.0, 200.0),
    ]


def test_shared_state_precomputes_returns_once():
    state = build_shared_state(_rows())
    assert state.symbols == ("A", "B")
    assert math.isclose(state.daily_return[("A", "2025-01-02")], 0.10)
    assert math.isclose(state.daily_return[("B", "2025-01-03")], -0.10)


def test_two_candidates_share_state_and_produce_distinct_results():
    state = build_shared_state(
        _rows(),
        [
            FundingEvent("A", "2025-01-02", 0.001),
            FundingEvent("B", "2025-01-02", -0.001),
        ],
    )

    def long_a(_date, _state):
        return {"A": 1.0, "B": 0.0}

    def long_b(_date, _state):
        return {"A": 0.0, "B": 1.0}

    results = run_weight_batch(
        state,
        {"long_a": long_a, "long_b": long_b},
        fee_rate=0.0,
        slippage_rate=0.0,
        terminal_liquidation=False,
    )

    assert set(results) == {"long_a", "long_b"}
    assert results["long_a"].final_equity > results["long_b"].final_equity
    assert results["long_a"].equity_curve == tuple(results["long_a"].equity_curve)
    assert results["long_a"].rebalance_count == 3


def test_batch_engine_applies_funding_before_rebalance():
    state = build_shared_state(
        _rows(),
        [FundingEvent("A", "2025-01-02", 0.10)],
    )

    def target(_date, _state):
        return {"A": 1.0, "B": 0.0}

    results = run_weight_batch(
        state,
        {"candidate": target},
        fee_rate=0.0,
        slippage_rate=0.0,
        terminal_liquidation=False,
    )

    # Position is established at the 2025-01-01 close; funding on 2025-01-02 is
    # therefore applied to that position before the 2025-01-02 rebalance.
    assert results["candidate"].funding_pnl_sum < 0.0
    assert results["candidate"].rebalance_count == 3


def test_batch_engine_rejects_non_finite_weights():
    state = build_shared_state(_rows())

    def bad(_date, _state):
        return {"A": float("nan"), "B": 0.0}

    try:
        run_weight_batch(
            state,
            {"bad": bad},
            fee_rate=0.0,
            slippage_rate=0.0,
            terminal_liquidation=False,
        )
    except ValueError as exc:
        assert "non-finite" in str(exc)
    else:
        raise AssertionError("expected non-finite candidate weights to be rejected")
