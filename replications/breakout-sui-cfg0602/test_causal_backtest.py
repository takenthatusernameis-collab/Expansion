#!/usr/bin/env python3
"""Deterministic unit tests for the causal SUI breakout simulator."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from causal_backtest import run_causal
from replicate import HOUR, iso


def candles_from(ohlc, base=1_700_000_000_000):
    rows = []
    for i, (open_price, high, low, close) in enumerate(ohlc):
        rows.append({
            "timestamp": base + i * HOUR,
            "open": float(open_price),
            "high": float(high),
            "low": float(low),
            "close": float(close),
            "volume": 1000.0,
        })
    return rows


class CausalBacktestTests(unittest.TestCase):
    def setUp(self):
        self.candles = candles_from([
            (100, 102, 99, 100),
            (100, 105, 99, 102),
            (102, 112, 101, 110),
            (110, 111, 108, 109),
        ])
        self.start = self.candles[0]["timestamp"]
        self.end = self.candles[-1]["timestamp"]

    def run_test_case(self, signals, multiplier=0.0, start=None, end=None):
        return run_causal(
            self.candles,
            signals,
            multiplier,
            self.start if start is None else start,
            self.end if end is None else end,
            exit_lookback=48,
            initial_capital=1000.0,
        )

    def test_signal_fills_only_at_next_bar_open(self):
        result = self.run_test_case({0}, multiplier=0.0)
        trade = result["trades"][0]
        signal_ms = self.candles[0]["timestamp"] + HOUR - 1
        entry_ms = self.candles[1]["timestamp"]
        self.assertEqual(trade["signal_time"], iso(signal_ms))
        self.assertEqual(trade["entry_time"], iso(entry_ms))
        self.assertGreater(entry_ms, signal_ms)
        self.assertEqual(result["equity_trace"][0]["equity"], 1000.0)
        self.assertEqual(result["equity_trace"][1]["equity"], 1000.0)
        # The first candle's close is marked before the next-open entry occurs.
        self.assertEqual(result["equity_trace"][2]["timestamp_ms"], entry_ms)
        self.assertEqual(result["equity_trace"][2]["equity"], 1000.0)
        self.assertAlmostEqual(result["net_return"], 0.09, places=10)

    def test_fees_and_slippage_are_deducted_once(self):
        result = self.run_test_case({0}, multiplier=1.0)
        trade_cost_fees = sum(t["entry_fee"] + t["exit_fee"] for t in result["trades"])
        trade_cost_slippage = sum(t["entry_slippage"] + t["exit_slippage"] for t in result["trades"])
        self.assertAlmostEqual(result["total_fees_quote"], trade_cost_fees, places=9)
        self.assertAlmostEqual(result["total_slippage_quote"], trade_cost_slippage, places=9)
        self.assertLess(result["net_return"], 0.10)
        self.assertGreaterEqual(result["final_equity_after_liquidation"], 0.0)

    def test_higher_cost_scenario_cannot_improve_same_event_schedule(self):
        base = self.run_test_case({0}, multiplier=1.0)
        stressed = self.run_test_case({0}, multiplier=3.0)
        self.assertLess(stressed["net_return"], base["net_return"])
        self.assertEqual(stressed["closed_trades"], base["closed_trades"])

    def test_pre_window_signal_does_not_create_a_trade_at_window_start(self):
        start = self.candles[1]["timestamp"]
        result = self.run_test_case({0}, multiplier=0.0, start=start)
        self.assertEqual(result["closed_trades"], 0)
        self.assertEqual(result["exposure_bars"], 0)

    def test_signal_inside_window_enters_on_following_open(self):
        result = self.run_test_case({1}, multiplier=0.0)
        self.assertEqual(result["closed_trades"], 1)
        self.assertEqual(result["trades"][0]["entry_time"], iso(self.candles[2]["timestamp"]))

    def test_last_bar_signal_is_not_filled_outside_evaluation_window(self):
        result = self.run_test_case({len(self.candles) - 1}, multiplier=0.0)
        self.assertEqual(result["closed_trades"], 0)
        self.assertTrue(result["unfilled_terminal_signal"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
