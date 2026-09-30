from experiments.run_harmony_deep_discovery_batch_006 import ols_beta, attention_ratio

def test_ols_beta_recovers_known_slope():
    x = [1.0, 2.0, 3.0, 4.0]
    y = [2.0, 4.0, 6.0, 8.0]
    assert ols_beta(y, x) == 2.0

def test_attention_ratio_uses_completed_prior_months_only():
    months = ["2024-01","2024-02","2024-03","2024-04","2024-05"]
    series = {m:10.0 for m in months}
    series["2024-04"] = 20.0
    assert attention_ratio(series, "2024-05", months) == 2.0

def test_attention_ratio_requires_four_completed_prior_slots():
    months = ["2024-01","2024-02","2024-03"]
    series = {m:10.0 for m in months}
    assert attention_ratio(series, "2024-03", months) is None
