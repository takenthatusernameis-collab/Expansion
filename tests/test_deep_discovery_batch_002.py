import io
import zipfile
from datetime import date

from experiments.run_harmony_deep_discovery_batch_002 import cot_normalize, metrics, segment_metrics, halves_metrics


def test_cot_header_normalization():
    assert cot_normalize("Noncommercial Positions-Long (All)") == "noncommercialpositionslongall"
    assert cot_normalize("As of Date in Form YYYY-MM-DD") == "asofdateinformyyyymmdd"


def test_metrics_is_deterministic():
    result = metrics([1.0, 1.01, 1.00, 1.02])
    assert result["observations"] == 4
    assert result["final_equity"] == 1.02


def test_oos_segment_includes_boundary_day_return():
    dates = ["2024-05-21", "2024-05-22", "2024-05-23"]
    equity = [2.0, 2.2, 2.0]
    result = segment_metrics(dates, equity, "2024-05-22")
    assert result["start"] == "2024-05-22"
    assert result["end"] == "2024-05-23"
    assert result["final_equity"] == 1.0

def test_oos_halves_are_explicit_and_contiguous():
    dates = [f"2024-05-{22+i:02d}" for i in range(8)]
    equity = [1.0 + 0.01 * i for i in range(8)]
    result = halves_metrics(dates, equity, "2024-05-22")
    assert result["first_half"]["end"] == result["split"]["first_half_end"]
    assert result["second_half"]["start"] == result["split"]["second_half_start"]

def test_mixed_binance_timestamp_units_are_supported():
    from datetime import datetime, timezone
    microseconds = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1_000_000)
    milliseconds = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1_000)
    def normalize(raw):
        seconds = raw / 1_000_000.0 if raw >= 100_000_000_000_000 else raw / 1_000.0
        return datetime.fromtimestamp(seconds, timezone.utc).date().isoformat()
    assert normalize(microseconds) == "2025-01-01"
    assert normalize(milliseconds) == "2024-01-01"

def test_zip_fixture_shape():
    payload = b"Market and Exchange Names,As of Date in Form YYYY-MM-DD\nBITCOIN - CHICAGO MERCANTILE EXCHANGE,2025-01-07\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("annual.txt", payload)
    assert buffer.getvalue()[:2] == b"PK"


def test_sparse_target_weights_are_treated_as_zero():
    from experiments.run_harmony_deep_discovery_batch_002 import simulate_panel
    dates = ["2024-05-21", "2024-05-22", "2024-05-23"]
    close = {
        "BTCUSDT": {"2024-05-21": 100.0, "2024-05-22": 101.0, "2024-05-23": 102.0},
        "ETHUSDT": {"2024-05-21": 100.0, "2024-05-22": 100.0, "2024-05-23": 100.0},
    }
    funding = {"BTCUSDT": {}, "ETHUSDT": {}}
    def weights(_i, _d):
        return {"BTCUSDT": 1.0}
    result = simulate_panel(dates, close, funding, weights, lambda i, _d: i == 1)
    assert result["metrics"]["observations"] == 3
