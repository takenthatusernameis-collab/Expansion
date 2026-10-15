import unittest

import numpy as np
import pandas as pd

from experiments.run_harmony_accel_leader_003 import (
    BAR_DELTA,
    SYMBOLS,
    compute_features,
    evaluate_promotion_gates,
    freshness_ok,
    ratchet_stop,
    simulate_segment,
    stop_trigger_price,
)


class AccelerationLeaderTests(unittest.TestCase):

    def test_preregistered_universe_has_exactly_24_symbols(self):
        expected = [
            "BTC-USDT", "ETH-USDT", "LTC-USDT", "XRP-USDT", "BNB-USDT", "BCH-USDT",
            "ADA-USDT", "DOGE-USDT", "SOL-USDT", "LINK-USDT", "DOT-USDT", "AVAX-USDT",
            "TRX-USDT", "ETC-USDT", "XLM-USDT", "ATOM-USDT", "FIL-USDT", "OP-USDT",
            "UNI-USDT", "AAVE-USDT", "ALGO-USDT", "NEAR-USDT", "MANA-USDT", "SAND-USDT",
        ]
        self.assertEqual(SYMBOLS, expected)

    def test_freshness_boundary_is_strict(self):
        self.assertTrue(freshness_ok(None, 10))
        self.assertFalse(freshness_ok(10, 106, window_bars=96))
        self.assertTrue(freshness_ok(10, 107, window_bars=96))

    def test_gap_through_stop_fills_at_open(self):
        self.assertEqual(stop_trigger_price(94.0, 90.0, 95.0), 94.0)
        self.assertEqual(stop_trigger_price(98.0, 94.0, 95.0), 95.0)
        self.assertIsNone(stop_trigger_price(98.0, 96.0, 95.0))

    def test_stop_never_moves_down(self):
        self.assertEqual(ratchet_stop(100.0, 102.0, 2.0, 3.0), 100.0)
        self.assertEqual(ratchet_stop(100.0, 110.0, 2.0, 3.0), 104.0)
        self.assertEqual(ratchet_stop(104.0, 107.0, 2.0, 3.0), 104.0)

    def test_acceleration_detects_return_rate_change_without_lookahead(self):
        index = pd.date_range("2023-01-01", periods=1920, freq=BAR_DELTA, tz="UTC")
        step = np.r_[np.full(1050, 0.0001), np.full(len(index) - 1050, 0.0005)]
        close = 100.0 * np.exp(np.cumsum(step))
        frame = pd.DataFrame({
            "ts_ms": index.asi8 // 1_000_000,
            "open": close * (1 - 0.0001),
            "high": close * (1 + 0.001),
            "low": close * (1 - 0.001),
            "close": close,
            "volume": np.full(len(index), 1000.0),
            "quote_volume": np.full(len(index), 100000.0),
        })
        feat = compute_features(frame, index)
        self.assertGreater(float(feat["acceleration"].max()), 0.0)
        self.assertTrue(np.isfinite(feat["z_acceleration"].to_numpy()).any())
        # Before the return-rate step, the acceleration proxy is approximately zero.
        self.assertLess(abs(float(feat["acceleration"].iloc[900])), 1e-9)

    def test_signal_executes_at_next_bar_open_not_signal_close(self):
        index = pd.date_range("2024-01-01", periods=12, freq=BAR_DELTA, tz="UTC")
        open_px = np.full(len(index), 100.0)
        close_px = np.full(len(index), 101.0)
        frame = pd.DataFrame({
            "open": open_px, "high": np.full(len(index), 102.0),
            "low": np.full(len(index), 99.0), "close": close_px,
            "atr": np.full(len(index), 5.0),
        }, index=index)
        features = {"BTC-USDT": frame}
        signal = {
            "symbol": "BTC-USDT", "signal_index": 2,
            "signal_time_utc": index[2].isoformat(), "signal_close": float(close_px[2]),
            "atr_at_signal": 5.0, "score": 2.0, "z_acceleration": 2.0,
            "m1h_log_return": 0.01,
        }
        result = simulate_segment(features, index, {2: signal}, 0, 10, 5.0)
        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual(result["trades"][0]["signal_time_utc"], index[2].isoformat())
        self.assertEqual(result["trades"][0]["entry_time_utc"], index[3].isoformat())

    def test_skip_if_next_open_has_already_crossed_initial_stop(self):
        index = pd.date_range("2024-01-01", periods=12, freq=BAR_DELTA, tz="UTC")
        open_px = np.full(len(index), 100.0)
        open_px[3] = 80.0
        close_px = np.full(len(index), 101.0)
        frame = pd.DataFrame({
            "open": open_px, "high": np.full(len(index), 103.0),
            "low": np.full(len(index), 79.0), "close": close_px,
            "atr": np.full(len(index), 5.0),
        }, index=index)
        signal = {
            "symbol": "BTC-USDT", "signal_index": 2,
            "signal_time_utc": index[2].isoformat(), "signal_close": 100.0,
            "atr_at_signal": 5.0, "score": 2.0, "z_acceleration": 2.0,
            "m1h_log_return": 0.01,
        }
        result = simulate_segment({"BTC-USDT": frame}, index, {2: signal}, 0, 10, 5.0)
        self.assertEqual(result["cancelled_gap_entries"], 1)
        self.assertEqual(result["trades"], [])


    @staticmethod
    def _gate_inputs(trades=50, primary_base=0.01, primary_high=0.002, sharpe_high=0.2,
                     comparator_base=-0.01, first_half=0.02, second_half=0.03, evidence=True):
        primary_base_metric = {"closed_trade_count": trades, "expectancy_per_trade": primary_base}
        primary_high_metric = {"expectancy_per_trade": primary_high, "annualized_sharpe": sharpe_high}
        comparator_metric = {"expectancy_per_trade": comparator_base}
        first_metric, second_metric = {"net_total_return": first_half}, {"net_total_return": second_half}
        return primary_base_metric, primary_high_metric, comparator_metric, first_metric, second_metric, evidence

    def test_all_predefined_gates_pass_only_when_every_absolute_and_relative_condition_passes(self):
        gates = evaluate_promotion_gates(*self._gate_inputs())
        self.assertTrue(gates["all_predefined_gates_pass"])
        self.assertTrue(gates["positive_absolute_primary_expectancy_at_base_5bps"])
        self.assertTrue(gates["positive_absolute_primary_expectancy_at_highest_20bps"])

    def test_trade_count_boundary_49_fails_and_50_passes(self):
        self.assertFalse(evaluate_promotion_gates(*self._gate_inputs(trades=49))["at_least_50_primary_final_oos_trades"])
        self.assertTrue(evaluate_promotion_gates(*self._gate_inputs(trades=50))["at_least_50_primary_final_oos_trades"])

    def test_zero_expectancy_fails_absolute_profitability_even_if_comparator_is_worse(self):
        gates = evaluate_promotion_gates(*self._gate_inputs(primary_base=0.0, comparator_base=-0.02))
        self.assertTrue(gates["primary_expectancy_exceeds_fresh_gainer_at_base_5bps"])
        self.assertFalse(gates["positive_absolute_primary_expectancy_at_base_5bps"])
        gates_both_lose = evaluate_promotion_gates(*self._gate_inputs(primary_base=-0.01, comparator_base=-0.02))
        self.assertTrue(gates_both_lose["primary_expectancy_exceeds_fresh_gainer_at_base_5bps"])
        self.assertFalse(gates_both_lose["positive_absolute_primary_expectancy_at_base_5bps"])
        self.assertFalse(gates_both_lose["all_predefined_gates_pass"])

    def test_zero_or_negative_high_cost_expectancy_and_zero_sharpe_fail(self):
        gates = evaluate_promotion_gates(*self._gate_inputs(primary_high=0.0, sharpe_high=0.0))
        self.assertFalse(gates["positive_absolute_primary_expectancy_at_highest_20bps"])
        self.assertFalse(gates["positive_primary_annualized_sharpe_at_highest_20bps"])
        self.assertFalse(gates["all_predefined_gates_pass"])

    def test_nonpositive_return_in_either_oos_half_fails(self):
        first_bad = evaluate_promotion_gates(*self._gate_inputs(first_half=0.0))
        self.assertFalse(first_bad["positive_primary_net_return_in_both_final_oos_halves_at_5bps"])
        second_bad = evaluate_promotion_gates(*self._gate_inputs(second_half=-0.001))
        self.assertFalse(second_bad["positive_primary_net_return_in_both_final_oos_halves_at_5bps"])

    def test_missing_preflight_evidence_fails_closed(self):
        gates = evaluate_promotion_gates(*self._gate_inputs(evidence=False))
        self.assertFalse(gates["pinned_preflight_evidence_valid"])
        self.assertFalse(gates["all_predefined_gates_pass"])


if __name__ == "__main__":
    unittest.main()
