import json
import tempfile
import unittest
from pathlib import Path

from experiments.preflight_harmony_accel_leader_003 import (
    BAR_MS, START_MS, SYMBOLS, check_candidate_symbols, timestamp_quality, write_json
)


class UniversePreflightTests(unittest.TestCase):
    def test_candidate_is_24_unique_symbols_and_replaces_eos(self):
        check_candidate_symbols()
        self.assertEqual(len(SYMBOLS), 24)
        self.assertEqual(len(SYMBOLS), len(set(SYMBOLS)))
        self.assertIn("OP-USDT", SYMBOLS)
        self.assertNotIn("EOS-USDT", SYMBOLS)

    def test_timestamp_quality_accepts_complete_aligned_series(self):
        timestamps = [START_MS + i * BAR_MS for i in range(10)]
        result = timestamp_quality(timestamps, start_ms=START_MS, now_ms=START_MS + 10 * BAR_MS)
        self.assertEqual(result["confirmed_rows"], 10)
        self.assertEqual(result["missing_candle_count"], 0)
        self.assertEqual(result["max_consecutive_missing_bars"], 0)

    def test_timestamp_quality_rejects_duplicate_timestamps(self):
        timestamps = [START_MS, START_MS + BAR_MS, START_MS + BAR_MS]
        with self.assertRaisesRegex(RuntimeError, "Duplicate"):
            timestamp_quality(timestamps, start_ms=START_MS, now_ms=START_MS + 10 * BAR_MS)

    def test_timestamp_quality_rejects_large_continuity_gap(self):
        timestamps = [START_MS + i * BAR_MS for i in range(10) if i != 5]
        with self.assertRaisesRegex(RuntimeError, "continuity gate failed"):
            timestamp_quality(timestamps, start_ms=START_MS, now_ms=START_MS + 10 * BAR_MS)

    def test_wrong_size_and_duplicate_universe_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "exactly 24"):
            check_candidate_symbols(["BTC-USDT"] * 23)
        with self.assertRaisesRegex(RuntimeError, "duplicate"):
            check_candidate_symbols(SYMBOLS[:-1] + [SYMBOLS[0]])

    def test_summary_evidence_writer_round_trips_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preflight_summary.json"
            write_json(path, {"status": "blocked_data_gate", "strategy_metrics_emitted": False})
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "blocked_data_gate")
            self.assertFalse(payload["strategy_metrics_emitted"])


if __name__ == "__main__":
    unittest.main()
