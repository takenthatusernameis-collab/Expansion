"""Deterministic tests for the Harmony execution substrate."""

from harmony_backtest.core import Bar, run_long_only


def test_one_profitable_trade_with_costs() -> None:
    bars = [
        Bar("2026-01-01", 100.0),
        Bar("2026-01-02", 110.0),
        Bar("2026-01-03", 110.0),
    ]
    result = run_long_only(
        bars,
        [True, False, False],
        initial_cash=1.0,
        fee_rate=0.01,
        slippage_rate=0.0,
    )
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert round(trade.gross_return, 10) == 0.10
    assert round(trade.net_return, 10) == round((0.99 ** 2) * 1.10 - 1.0, 10)
    assert round(result.cumulative_return, 10) == round(trade.net_return, 10)


def test_flat_signal_has_no_trades() -> None:
    bars = [Bar("2026-01-01", 100.0), Bar("2026-01-02", 101.0)]
    result = run_long_only(bars, [False, False])
    assert result.trades == ()
    assert result.final_cash == 1.0


def test_strict_timestamp_validation() -> None:
    bars = [Bar("2026-01-02", 100.0), Bar("2026-01-01", 101.0)]
    try:
        run_long_only(bars, [False, False])
    except ValueError as exc:
        assert "strictly increasing" in str(exc)
    else:
        raise AssertionError("expected timestamp validation failure")


def test_signal_length_is_part_of_experiment_identity() -> None:
    bars = [Bar("2026-01-01", 100.0), Bar("2026-01-02", 101.0)]
    try:
        run_long_only(bars, [True])
    except ValueError as exc:
        assert "signals length" in str(exc)
    else:
        raise AssertionError("expected signal-length validation failure")


def test_entry_and_exit_slippage_are_applied() -> None:
    bars = [
        Bar("2026-01-01", 100.0),
        Bar("2026-01-02", 110.0),
    ]
    result = run_long_only(
        bars,
        [True, False],
        initial_cash=1.0,
        fee_rate=0.0,
        slippage_rate=0.01,
    )
    trade = result.trades[0]
    assert trade.entry_price == 101.0
    assert trade.exit_price == 108.9
    assert round(trade.gross_return, 10) == round(108.9 / 101.0 - 1.0, 10)
