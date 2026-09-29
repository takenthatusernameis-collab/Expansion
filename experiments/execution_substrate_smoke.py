"""Execution-plane smoke experiment.

This is an infrastructure acceptance run, not evidence of a profitable strategy.
"""

from harmony_backtest.core import Bar, run_long_only


BARS = (
    Bar("2026-01-01", 100.0),
    Bar("2026-01-02", 102.0),
    Bar("2026-01-03", 101.0),
)

SIGNALS = (True, False, False)


if __name__ == "__main__":
    result = run_long_only(BARS, SIGNALS, initial_cash=1.0, fee_rate=0.001)
    print(
        {
            "experiment_type": "execution_substrate_smoke",
            "final_cash": result.final_cash,
            "cumulative_return": result.cumulative_return,
            "trade_count": len(result.trades),
        }
    )
