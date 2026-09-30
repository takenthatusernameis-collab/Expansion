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

def test_zip_fixture_shape():
    payload = b"Market and Exchange Names,As of Date in Form YYYY-MM-DD\nBITCOIN - CHICAGO MERCANTILE EXCHANGE,2025-01-07\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("annual.txt", payload)
    assert buffer.getvalue()[:2] == b"PK"
