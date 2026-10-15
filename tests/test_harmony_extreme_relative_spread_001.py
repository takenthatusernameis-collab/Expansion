import unittest

import numpy as np
import pandas as pd

from experiments.run_harmony_extreme_relative_spread_001 import (
    build_pairs,
    evaluate_gates,
    lagged_spread_returns,
    primary_turn_direction,
)


class RelativeSpreadTests(unittest.TestCase):
    def test_pair_ranking_has_deterministic_lexicographic_ties(self):
        scores = np.array([[0.4, 0.4, -0.2], [0.5, 0.1, -0.1]], dtype=float)
        top, bottom = build_pairs(scores, ["ZED", "ALPHA", "BETA"])
        self.assertEqual(top.tolist(), [1, 0])
        self.assertEqual(bottom.tolist(), [2, 2])

    def test_interval_return_uses_previous_bar_pair_not_current_pair(self):
        close = np.array([
            [100.0, 100.0, 100.0],
            [110.0, 90.0, 100.0],
            [121.0, 81.0, 100.0],
        ])
        top = np.array([0, 1, 0])
        bottom = np.array([1, 2, 2])
        ret = lagged_spread_returns(close, top, bottom)
        self.assertAlmostEqual(ret[1], np.log(110.0 / 100.0) - np.log(90.0 / 100.0))
        self.assertAlmostEqual(ret[2], np.log(81.0 / 90.0) - np.log(100.0 / 100.0))

    def test_primary_requires_local_turn_aligned_with_slow_state(self):
        self.assertEqual(primary_turn_direction(0.04, 0.01, -0.001), 1)
        self.assertEqual(primary_turn_direction(-0.04, -0.01, 0.001), -1)
        self.assertEqual(primary_turn_direction(0.04, 0.01, 0.001), 0)
        self.assertEqual(primary_turn_direction(-0.04, 0.01, -0.001), 0)
        self.assertEqual(primary_turn_direction(0.0, 0.01, -0.001), 0)

    @staticmethod
    def gate_inputs(trades=50, expectancy=0.01, base_return=0.10, high_return=0.02,
                    high_sharpe=0.5, first_half=0.02, second_half=0.03,
                    primary_ci=[0.001, 0.01], diff_ci=[0.001, 0.02],
                    evidence=True, breach=0, comparator_return=0.01):
        primary_base = {
            "closed_trade_count": trades, "expectancy_per_trade": expectancy,
            "net_total_return": base_return, "margin_breach_or_ruin_count": breach,
        }
        primary_high = {
            "net_total_return": high_return, "annualized_sharpe": high_sharpe,
            "margin_breach_or_ruin_count": breach,
        }
        comparator = {"net_total_return": comparator_return}
        first = {"net_total_return": first_half}
        second = {"net_total_return": second_half}
        bootstrap = {
            "primary_mean_daily_return_ci_95": primary_ci,
            "primary_minus_hourly_breakout_mean_daily_return_ci_95": diff_ci,
        }
        return primary_base, primary_high, comparator, first, second, bootstrap, evidence

    def test_all_gates_must_pass(self):
        self.assertTrue(evaluate_gates(*self.gate_inputs())["all_predefined_gates_pass"])

    def test_49_trade_boundary_fails_and_50_passes(self):
        self.assertFalse(evaluate_gates(*self.gate_inputs(trades=49))["at_least_50_primary_final_oos_trades"])
        self.assertTrue(evaluate_gates(*self.gate_inputs(trades=50))["at_least_50_primary_final_oos_trades"])

    def test_zero_expectancy_fails_even_if_other_metrics_are_good(self):
        gates = evaluate_gates(*self.gate_inputs(expectancy=0.0))
        self.assertFalse(gates["positive_absolute_primary_expectancy_at_5bps"])
        self.assertFalse(gates["all_predefined_gates_pass"])

    def test_ci_touching_zero_fails(self):
        gates = evaluate_gates(*self.gate_inputs(primary_ci=[0.0, 0.02]))
        self.assertFalse(gates["primary_daily_return_bootstrap_ci_lower_above_zero"])

    def test_not_beating_baseline_fails(self):
        gates = evaluate_gates(*self.gate_inputs(diff_ci=[-0.01, 0.02]))
        self.assertFalse(gates["primary_beats_hourly_breakout_bootstrap_ci_lower_above_zero"])

    def test_high_cost_nonpositive_return_or_sharpe_fails(self):
        gates = evaluate_gates(*self.gate_inputs(high_return=0.0))
        self.assertFalse(gates["positive_primary_net_return_at_20bps"])
        gates = evaluate_gates(*self.gate_inputs(high_sharpe=0.0))
        self.assertFalse(gates["positive_primary_annualized_sharpe_at_20bps"])

    def test_either_oos_half_nonpositive_fails(self):
        gates = evaluate_gates(*self.gate_inputs(first_half=0.0))
        self.assertFalse(gates["positive_primary_net_return_in_both_final_oos_halves_at_5bps"])
        gates = evaluate_gates(*self.gate_inputs(second_half=-0.01))
        self.assertFalse(gates["positive_primary_net_return_in_both_final_oos_halves_at_5bps"])

    def test_any_margin_breach_fails(self):
        gates = evaluate_gates(*self.gate_inputs(breach=1))
        self.assertFalse(gates["no_primary_margin_breach_or_account_ruin"])

    def test_invalid_data_evidence_fails_closed(self):
        gates = evaluate_gates(*self.gate_inputs(evidence=False))
        self.assertFalse(gates["pinned_data_evidence_valid"])
        self.assertFalse(gates["all_predefined_gates_pass"])


if __name__ == "__main__":
    unittest.main()
