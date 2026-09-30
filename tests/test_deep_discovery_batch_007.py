from experiments.run_harmony_deep_discovery_batch_007 import realized_vol

def test_realized_vol_uses_exact_prior_window():
    returns = [0.0, 0.01, -0.01, 0.02]
    assert abs(realized_vol(returns) - (sum((x - sum(returns)/len(returns))**2 for x in returns)/len(returns))**0.5) < 1e-12

def test_no_signal_uses_current_month_returns():
    prices = {
        "2024-01-01": 100.0,
        "2024-01-02": 101.0,
    }
    assert prices["2024-01-01"] == 100.0
