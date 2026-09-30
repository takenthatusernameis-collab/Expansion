import io
import zipfile
from datetime import date

from experiments.run_harmony_deep_discovery_batch_002 import cot_normalize, metrics


def test_cot_header_normalization():
    assert cot_normalize("Noncommercial Positions-Long (All)") == "noncommercialpositionslongall"
    assert cot_normalize("As of Date in Form YYYY-MM-DD") == "asofdateinformyyyymmdd"


def test_metrics_is_deterministic():
    result = metrics([1.0, 1.01, 1.00, 1.02])
    assert result["observations"] == 4
    assert result["final_equity"] == 1.02


def test_zip_fixture_shape():
    payload = b"Market and Exchange Names,As of Date in Form YYYY-MM-DD\nBITCOIN - CHICAGO MERCANTILE EXCHANGE,2025-01-07\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("annual.txt", payload)
    assert buffer.getvalue()[:2] == b"PK"
