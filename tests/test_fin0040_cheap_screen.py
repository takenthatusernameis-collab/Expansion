from experiments.run_harmony_fin0040_cheap_screen import skew_unbiased

def test_adjusted_fisher_pearson_matches_known_sample():
    xs = [1.0, 2.0, 3.0, 4.0, 100.0]
    value = skew_unbiased(xs)
    assert value > 1.5

def test_zero_variance_is_zero_skew():
    assert skew_unbiased([3.0, 3.0, 3.0]) == 0.0
