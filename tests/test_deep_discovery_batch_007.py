from experiments.run_harmony_deep_discovery_batch_007 import realized_vol

def test_realized_vol_uses_exact_prior_window():
    returns = [0.0, 0.01, -0.01, 0.02]
    assert abs(realized_vol(returns) - (sum((x - sum(returns)/len(returns))**2 for x in returns)/len(returns))**0.5) < 1e-12

def test_btc_benchmark_target_covers_all_symbols():
    from experiments.run_harmony_deep_discovery_batch_007 import SYMBOLS, btc_targets
    target = btc_targets(["2024-01"])["2024-01"]
    assert target["BTCUSDT"] == 1.0
    assert sum(target[s] for s in SYMBOLS if s != "BTCUSDT") == 0.0
