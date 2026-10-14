import csv
import gzip
import json
import tempfile
import unittest
from unittest.mock import patch

import experiments.preflight_harmony_accel_leader_003 as preflight
from pathlib import Path

from experiments.preflight_harmony_accel_leader_003 import (
    BAR_MS, START_MS, SYMBOLS, check_candidate_symbols, enforce_joint_coverage, timestamp_quality, write_json
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

    def test_joint_coverage_accepts_exact_threshold(self):
        self.assertAlmostEqual(enforce_joint_coverage(999, 1000), 0.001)

    def test_joint_coverage_rejects_fraction_above_threshold(self):
        with self.assertRaisesRegex(RuntimeError, "Joint common-panel coverage gate failed"):
            enforce_joint_coverage(998, 1000)

    def test_complete_cached_candle_file_is_revalidated_and_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "bars" / "BTC_USDT.csv.gz"
            path.parent.mkdir(parents=True)
            with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, lineterminator="\n")
                writer.writerow(["ts_ms", "open", "high", "low", "close", "volume", "quote_volume"])
                writer.writerow([START_MS, "100", "102", "99", "101", "10", "1000"])
                writer.writerow([START_MS + BAR_MS, "101", "103", "100", "102", "11", "1100"])
            quality = {
                "first_candle_open_ms": START_MS,
                "last_confirmed_candle_open_ms": START_MS + BAR_MS,
                "confirmed_rows": 2, "expected_grid_rows": 2,
                "missing_candle_count": 0, "missing_fraction": 0.0,
                "max_consecutive_missing_bars": 0, "last_confirmed_bar_age_hours": 0.0,
            }
            with patch.object(preflight, "OUT", root), patch.object(preflight, "timestamp_quality", return_value=quality):
                timestamps, info = preflight.load_cached_symbol("BTC-USDT")
            self.assertEqual(list(timestamps), [START_MS, START_MS + BAR_MS])
            self.assertTrue(info["cache_reused"])
            expected = (
                f"ts_ms,open,high,low,close,volume,quote_volume\n"
                f"{START_MS},100,102,99,101,10,1000\n"
                f"{START_MS + BAR_MS},101,103,100,102,11,1100\n"
            ).encode("utf-8")
            self.assertEqual(info["canonical_uncompressed_csv_bytes"], len(expected))

    def test_summary_evidence_writer_round_trips_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preflight_summary.json"
            write_json(path, {"status": "blocked_data_gate", "strategy_metrics_emitted": False})
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "blocked_data_gate")
            self.assertFalse(payload["strategy_metrics_emitted"])


if __name__ == "__main__":
    unittest.main()
