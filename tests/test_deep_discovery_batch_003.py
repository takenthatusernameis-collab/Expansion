from datetime import date

from experiments.run_harmony_deep_discovery_batch_003 import (
    parse_alfred_csv,
    parse_stablecoin_series,
    signal_map_from_m2,
    signal_map_from_stablecoin,
)


def test_parse_stablecoin_series():
    raw = b'[{"date":"1704067200","totalCirculatingUSD":{"peggedUSD":100.0}},{"date":"1704672000","totalCirculatingUSD":{"peggedUSD":105.0}}]'
    rows = parse_stablecoin_series(raw)
    assert rows[0][0] == "2024-01-01"
    assert rows[1][1] == 105.0


def test_parse_alfred_csv():
    raw = b"observation_date,M2SL\n2024-01-01,21000\n2024-02-01,21100\n"
    rows = parse_alfred_csv(raw)
    assert rows[-1] == ("2024-02-01", 21100.0)


def test_stablecoin_signal_is_weekly_not_daily():
    series = [
        (f"2024-01-{day:02d}", 100.0 + day)
        for day in range(1, 25)
    ]
    btc_dates = [f"2024-01-{day:02d}" for day in range(10, 25)]
    signals = signal_map_from_stablecoin(series, btc_dates)
    assert len(signals) >= 1
    ordered = [date.fromisoformat(d) for d in signals]
    assert all((b - a).days == 7 for a, b in zip(ordered, ordered[1:]))


def test_m2_signal_activates_only_once_after_each_vintage():
    signals = signal_map_from_m2(
        [
            {"vintage_date": "2024-01-31", "growth": 0.01},
            {"vintage_date": "2024-02-29", "growth": -0.01},
        ],
        ["2024-01-31", "2024-02-01", "2024-02-02", "2024-03-01", "2024-03-02"],
    )
    assert signals["2024-02-01"] == 1.0
    assert signals["2024-03-01"] == 0.0
    assert len(signals) == 2
