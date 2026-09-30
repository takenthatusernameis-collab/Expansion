from experiments.run_harmony_deep_discovery_batch_005 import (
    rolling_mean,
    build_signal_map,
    metrics,
)

def test_rolling_mean_is_prior_only():
    values = {"2024-01-01": 1.0, "2024-01-02": 2.0, "2024-01-03": 3.0}
    dates = sorted(values)
    assert rolling_mean(values, dates, 2, 2) == 1.5

def test_signal_map_has_no_short_state():
    dates = [f"2024-01-{i:02d}" for i in range(1, 15)]
    ind = {d: float(i) for i, d in enumerate(dates)}
    signals = build_signal_map(
        dates, ind, smooth_window=1, recent_window=5, rebalance_every=7
    )
    assert set(signals.values()).issubset({0.0, 1.0})

def test_metrics_deterministic():
    result = metrics([1.0, 1.01, 1.0, 1.02])
    assert result["observations"] == 4
    assert "sharpe" in result


def test_signal_activates_after_indicator_observation():
    dates = [f"2024-01-{i:02d}" for i in range(1, 15)]
    ind = {d: float(i) for i, d in enumerate(dates)}
    signals = build_signal_map(
        dates, ind, smooth_window=1, recent_window=5, rebalance_every=7
    )
    assert all(d != dates[0] for d in signals)
    signal_dates = sorted(signals)
    assert all(dates.index(d) > 9 for d in signal_dates)
