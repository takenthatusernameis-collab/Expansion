from pathlib import Path

SCRIPT=Path(__file__).parents[1]/"experiments/run_harmony_elapsed_history_001.py"
PROTOCOL=Path(__file__).parents[1]/"research/HARMONY-ELAPSED-HISTORY-001.yaml"

def test_elapsed_scope_is_data_only():
    text=SCRIPT.read_text()
    assert "candidate_execution_authorized" in text
    assert "oos_claim_authorized" in text
    assert '"HARMONY-USDM-DAILY-FUNDING-2026-09-ELAPSED"' in text

def test_daily_archive_family_is_used():
    text=SCRIPT.read_text()
    assert "/daily/klines/" in text
    assert "/daily/fundingRate/" in text
    assert ".CHECKSUM" in text

def test_protocol_has_frozen_elapsed_scope():
    text=PROTOCOL.read_text()
    assert 'start: "2026-09-01"' in text
    assert 'end: "2026-09-29"' in text
    assert "candidate_execution_authorized: false" in text
