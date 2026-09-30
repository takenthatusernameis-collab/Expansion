import math
import unittest

from experiments.run_harmony_fin_0013 import normalized_segment_metrics, oos_report


def fixed_weights(close, i, symbols):
    if i < 21:
        return None
    signs = {}
    for s in symbols:
        r = close[s][i - 1] / close[s][i - 21] - 1.0
        signs[s] = 1 if r > 0 else (-1 if r < 0 else 0)

    pos = [s for s in symbols if signs[s] > 0]
    neg = [s for s in symbols if signs[s] < 0]
    w = {s: 0.0 for s in symbols}

    if pos and neg:
        for s in pos:
            w[s] = 0.5 / len(pos)
        for s in neg:
            w[s] = -0.5 / len(neg)
    elif pos:
        for s in pos:
            w[s] = 1.0 / len(pos)
    elif neg:
        for s in neg:
            w[s] = -1.0 / len(neg)

    return w


class FIN0013PortfolioTests(unittest.TestCase):
    def test_fixed_tsmom_weights(self):
        symbols = ["A", "B", "C", "D"]
        n = 25
        close = {
            "A": [100 + i for i in range(n)],
            "B": [100 - i for i in range(n)],
            "C": [100 for _ in range(n)],
            "D": [100 + (i % 2) for i in range(n)],
        }
        w = fixed_weights(close, 21, symbols)
        self.assertGreater(w["A"], 0)
        self.assertLess(w["B"], 0)
        self.assertEqual(w["C"], 0)
        self.assertTrue(math.isclose(sum(x for x in w.values() if x > 0), 0.5))
        self.assertTrue(math.isclose(sum(-x for x in w.values() if x < 0), 0.5))

    def test_oos_segment_metrics_normalize_to_one(self):
        rows = [
            ("2024-05-21", 2.0),
            ("2024-05-22", 2.2),
            ("2024-05-23", 2.0),
        ]
        result = normalized_segment_metrics(rows, "2024-05-22", "2024-05-23")
        self.assertEqual(result["start"], "2024-05-22")
        self.assertEqual(result["end"], "2024-05-23")
        self.assertTrue(math.isclose(result["final_equity"], 1.0))

    def test_oos_report_has_two_fixed_halves(self):
        rows = [(f"2024-05-{22+i:02d}", 1.0 + i * 0.01) for i in range(8)]
        report = oos_report(rows)
        self.assertIn("full_oos", report)
        self.assertIn("first_half", report)
        self.assertIn("second_half", report)
        self.assertEqual(report["first_half"]["end"], report["split"]["first_half_end"])
        self.assertEqual(report["second_half"]["start"], report["split"]["second_half_start"])

    def test_single_side_normalization(self):
        symbols = ["A", "B"]
        close = {
            "A": [100 + i for i in range(25)],
            "B": [100 + i for i in range(25)],
        }
        w = fixed_weights(close, 21, symbols)
        self.assertTrue(math.isclose(sum(x for x in w.values() if x > 0), 1.0))
        self.assertTrue(all(x >= 0 for x in w.values()))


if __name__ == "__main__":
    unittest.main()
