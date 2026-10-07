from __future__ import annotations

import math

import pandas as pd

from experiments.harmony_soxl_intraday_horizon_001 import horizon_exposure


def test_horizon_vote_uses_only_completed_bars():
    closes = pd.Series([100.0] * 400)
    t = 366
    for lookback in (30, 90, 365):
        closes.iloc[t - 1 - lookback] = 99.0
    assert math.isclose(horizon_exposure(closes, 366), 1.0)


def test_horizon_vote_is_neutral_before_longest_horizon():
    closes = pd.Series([100.0] * 365)
    assert horizon_exposure(closes, 364) == 0.0


def test_two_positive_votes_map_to_two_thirds():
    closes = pd.Series([100.0] * 400)
    t = 366
    closes.iloc[t - 1 - 30] = 99.0
    closes.iloc[t - 1 - 90] = 99.0
    assert math.isclose(horizon_exposure(closes, 366), 2.0 / 3.0)
