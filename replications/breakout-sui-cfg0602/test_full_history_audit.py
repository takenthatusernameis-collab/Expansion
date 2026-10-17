#!/usr/bin/env python3
"""Offline deterministic tests for full-history data integrity and pagination."""
import json
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

HERE = __import__("pathlib").Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import full_history_audit as audit
from replicate import HOUR


def candle(timestamp, open_price=100.0, high=102.0, low=99.0, close=101.0, volume=10.0):
    return {
        "timestamp": timestamp,
        "open": float(open_price),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "volume": float(volume),
    }


def api_row(timestamp, open_price=100.0, high=102.0, low=99.0, close=101.0, volume=10.0, microseconds=False):
    scale = 1000 if microseconds else 1
    return [
        timestamp * scale, str(open_price), str(high), str(low), str(close), str(volume),
        (timestamp + HOUR - 1) * scale, "1000", 10, "4", "400", "0",
    ]


class FakeResponse:
    status = 200

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.body


class FullHistoryAuditTests(unittest.TestCase):
    def setUp(self):
        self.start = int(datetime(2023, 5, 3, 12, tzinfo=timezone.utc).timestamp() * 1000)
        self.rows = [candle(self.start + i * HOUR, 100 + i, 103 + i, 99 + i, 101 + i, 10 + i)
                     for i in range(4)]

    def test_latest_closed_hour_excludes_partial_current_candle(self):
        now = int(datetime(2026, 10, 17, 14, 52, tzinfo=timezone.utc).timestamp() * 1000)
        expected = int(datetime(2026, 10, 17, 13, tzinfo=timezone.utc).timestamp() * 1000)
        self.assertEqual(audit.latest_closed_open_ms(now), expected)

    def test_latest_closed_hour_at_boundary_uses_previous_open(self):
        now = int(datetime(2026, 10, 17, 14, 0, tzinfo=timezone.utc).timestamp() * 1000)
        expected = int(datetime(2026, 10, 17, 13, tzinfo=timezone.utc).timestamp() * 1000)
        self.assertEqual(audit.latest_closed_open_ms(now), expected)

    def test_api_row_parser_normalizes_microsecond_timestamps(self):
        row = api_row(self.start, microseconds=True)
        parsed = audit.parse_api_kline(row)
        self.assertEqual(parsed["timestamp"], self.start)
        self.assertEqual(parsed["close"], 101.0)

    def test_series_rejects_a_gap(self):
        with self.assertRaisesRegex(ValueError, "Missing hourly"):
            audit.validate_series([self.rows[0], self.rows[2]])

    def test_series_rejects_duplicate_timestamp(self):
        with self.assertRaisesRegex(ValueError, "Duplicate or out-of-order"):
            audit.validate_series([self.rows[0], self.rows[0]])

    def test_series_rejects_invalid_ohlc(self):
        bad = candle(self.start, 100, 98, 99, 101, 10)
        with self.assertRaisesRegex(ValueError, "Invalid OHLC"):
            audit.validate_series([bad])

    def test_coverage_requires_exact_listing_and_latest_candle_boundaries(self):
        with self.assertRaisesRegex(ValueError, "expected listing-hour"):
            audit.validate_series(self.rows, self.start + HOUR, self.rows[-1]["timestamp"])

    def test_api_pagination_has_no_overlap_or_skipped_rows(self):
        base = "https://fixture.invalid/api/v3/klines"

        def fake_urlopen(request, timeout):
            params = parse_qs(urlparse(request.full_url).query)
            self.assertEqual(params["symbol"][0], "SUIUSDT")
            cursor = int(params["startTime"][0])
            end_ms = int(params["endTime"][0])
            limit = int(params["limit"][0])
            raw_rows = [
                api_row(item["timestamp"], item["open"], item["high"], item["low"], item["close"], item["volume"])
                for item in self.rows if cursor <= item["timestamp"] <= end_ms
            ][:limit]
            return FakeResponse(json.dumps(raw_rows).encode("utf-8"))

        with patch("full_history_audit.urllib.request.urlopen", side_effect=fake_urlopen):
            rows, requests_made = audit.fetch_klines_from_base(
                base, "SUIUSDT", self.start, self.rows[-1]["timestamp"], page_limit=2
            )
        self.assertEqual(len(rows), 4)
        self.assertEqual(requests_made, 2)
        self.assertEqual([row["timestamp"] for row in rows], [row["timestamp"] for row in self.rows])

    def test_overlap_check_detects_price_mismatch(self):
        full = [dict(row) for row in self.rows]
        pinned = [dict(row) for row in self.rows]
        pinned[1]["close"] += 0.01
        result = audit.compare_overlap(full, pinned)
        self.assertEqual(result["overlap_rows_compared"], 4)
        self.assertEqual(result["mismatch_rows"], 1)

    def test_buy_and_hold_cost_drag_is_monotonic(self):
        rows = [
            candle(self.start, 100, 102, 99, 101, 10),
            candle(self.start + HOUR, 101, 112, 100, 110, 11),
        ]
        zero = audit.benchmark_metrics(rows, 0.0)["net_return"]
        one = audit.benchmark_metrics(rows, 1.0)["net_return"]
        two = audit.benchmark_metrics(rows, 2.0)["net_return"]
        three = audit.benchmark_metrics(rows, 3.0)["net_return"]
        self.assertGreater(zero, one)
        self.assertGreater(one, two)
        self.assertGreater(two, three)


if __name__ == "__main__":
    unittest.main()
