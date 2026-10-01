from experiments.run_harmony_fin0056_durability_001 import population_kurtosis

def test_kurtosis_smoke():
    assert population_kurtosis([1.0, 2.0, 3.0, 4.0]) > 1.0
