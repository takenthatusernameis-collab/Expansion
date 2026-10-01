from experiments.run_harmony_fin0056_promoted_validation_001 import metrics

def test_metrics_smoke():
    assert metrics([1.0,1.01,1.02])["observations"] == 3
