import numpy as np
import pandas as pd
import pytest

from experiments import run_harmony_mom121_deep_grind_001 as mom


def test_mom121_uses_prior_11_returns_and_excludes_current_month():
    months = list(pd.period_range("2020-01", periods=13, freq="M"))
    values = np.array([0.01 * (i + 1) for i in range(13)], dtype=float)
    panel = pd.DataFrame({
        "security_id": ["A"] * len(months),
        "month": months,
        "total_return": values,
    })
    s1 = mom.compute_scores(panel)
    expected = float(np.prod(1 + values[0:11]) - 1)
    assert s1[(months[11], "A")] == pytest.approx(expected)

    changed = values.copy()
    changed[11] = 0.80
    panel["total_return"] = changed
    s2 = mom.compute_scores(panel)
    assert s2[(months[11], "A")] == pytest.approx(expected)


def test_mom121_skips_security_with_gap_in_prior_months():
    months = list(pd.period_range("2020-01", periods=13, freq="M"))
    keep = [i for i in range(13) if i != 5]
    panel = pd.DataFrame({
        "security_id": ["A"] * len(keep),
        "month": [months[i] for i in keep],
        "total_return": [0.01] * len(keep),
    })
    scores = mom.compute_scores(panel)
    assert (months[12], "A") not in scores


def test_missing_point_in_time_panel_blocks_instead_of_fabricating_results(tmp_path, monkeypatch):
    monkeypatch.delenv("MOM121_DATA_ARCHIVE_URL", raising=False)
    monkeypatch.delenv("MOM121_DATA_ARCHIVE_SHA256", raising=False)
    monkeypatch.setattr(mom, "DATA", tmp_path / "missing")
    with pytest.raises(mom.DataGateError, match="No point-in-time US microcap panel"):
        mom.load_panel()


def test_random_placebo_preserves_eligible_count_and_equal_weights():
    months = list(pd.period_range("2023-01", periods=2, freq="M"))
    eligible = {months[0]: [f"S{i:02d}" for i in range(10)], months[1]: []}
    targets = mom.placebo_targets(eligible, seed=121)
    assert len(targets[months[0]]) == 2
    assert sum(targets[months[0]].values()) == pytest.approx(1.0)
    assert targets[months[1]] == {}


def test_manifest_boolean_parser_is_fail_closed():
    assert mom.get_bool(True) is True
    assert mom.get_bool("false") is False
    assert mom.get_bool("maybe") is None
    assert mom.get_bool(None) is None



def test_capacity_diagnostics_recalculate_adv_threshold_for_each_account_size():
    events = [
        {"order_notional_usd": 2000.0, "adv20_usd": 100000.0},
        {"order_notional_usd": 8000.0, "adv20_usd": 1000000.0},
    ]
    result = mom.capacity_diagnostics([{"order_events": events}])
    assert result["100000"]["fraction_order_notional_over_1pct_adv"] == pytest.approx(0.0)
    assert result["1000000"]["fraction_order_notional_over_1pct_adv"] == pytest.approx(0.2)
    assert result["10000000"]["fraction_order_notional_over_1pct_adv"] == pytest.approx(1.0)
    assert result["10000000"]["max_observed_order_participation_adv"] == pytest.approx(0.2)
