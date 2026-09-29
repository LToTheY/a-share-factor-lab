"""Check investable label timing, overlap uncertainty and new factor definitions."""

import numpy as np
import pandas as pd
import pytest

from quant_lab.evaluation.diagnostics import (
    add_execution_returns,
    hac_mean_summary,
    quantile_returns,
)
from quant_lab.factors.library import compute_factor


def test_execution_label_skips_signal_close_and_stops_at_segments():
    frame = pd.DataFrame({"trade_date": pd.bdate_range("2025-01-02", periods=7), "symbol": "A",
                              "adj_open": [1., 10., 11., 12., 20., 22., 24.], "research_segment": [0, 0, 0, 0, 1, 1, 1]})
    result = add_execution_returns(frame, 2)
    assert result.iloc[0].next_open_return_2d == pytest.approx(.2)
    assert np.isnan(result.iloc[1].next_open_return_2d)
    assert np.isnan(result.iloc[4].next_open_return_2d)


def test_hac_zero_lag_matches_sample_mean_standard_error():
    values = pd.Series([.1, .2, -.1, .3, .0])
    result = hac_mean_summary(values, 0)
    assert result["hac_se"] == pytest.approx(values.std(ddof=1) / np.sqrt(len(values)))
    assert result["hac_mean"] == pytest.approx(values.mean())


def test_hac_accounts_for_positive_serial_dependence():
    values = pd.Series(np.repeat([-.1, .0, .1, .2], 30))
    assert hac_mean_summary(values, 4)["hac_se"] > hac_mean_summary(values, 0)["hac_se"]
    assert hac_mean_summary(pd.Series([np.nan, .2]), 4)["hac_observations"] == 1
    assert np.isnan(hac_mean_summary(pd.Series([np.nan, .2]), 4)["hac_t"])


def test_quantiles_are_stable_under_input_shuffle_and_constant_is_uninformative():
    frame = pd.DataFrame({"trade_date": pd.Timestamp("2025-01-02"), "symbol": list("abcdef"), "factor": [1, 1, 2, 2, 3, 3], "label": np.arange(6)})
    pd.testing.assert_frame_equal(quantile_returns(frame, "factor", "label", 3), quantile_returns(frame.sample(frac=1, random_state=4), "factor", "label", 3))
    frame["factor"] = 1
    assert quantile_returns(frame, "factor", "label", 3).empty


def test_missing_future_labels_cannot_change_signal_day_groups():
    frame = pd.DataFrame({"trade_date": pd.Timestamp("2025-01-02"), "symbol": list("abcdef"),
                          "factor": [1., 2., 3., 4., 5., 6.], "label": [np.nan, 2., 3., 4., 5., 6.]})
    result = quantile_returns(frame, "factor", "label", 2).set_index("quantile")
    assert result.at[1, "label"] == pytest.approx(2.5)
    assert result.at[2, "label"] == pytest.approx(5.)
    assert result.at[1, "assigned_count"] == 3
    assert result.at[1, "observed_count"] == 2
    assert result.at[1, "label_coverage"] == pytest.approx(2 / 3)
    frame["label"] = np.nan
    missing = quantile_returns(frame, "factor", "label", 2)
    assert len(missing) == 2 and missing["label"].isna().all()
    assert missing.observed_count.eq(0).all()


@pytest.mark.parametrize("name", ["downside_volatility_20", "overnight_reversal_5", "intraday_momentum_20"])
def test_new_factors_are_prefix_invariant_and_restart_after_gaps(name):
    days = pd.bdate_range("2025-01-02", periods=80)
    frame = pd.DataFrame({"trade_date": days, "symbol": "A", "open": 10., "adj_open": 10., "close": 10+np.sin(np.arange(80)), "adj_close": 10+np.sin(np.arange(80)), "research_segment": [0]*40+[1]*40})
    full = compute_factor(frame, name)
    prefix = compute_factor(frame.iloc[:32], name)
    pd.testing.assert_frame_equal(full.iloc[:32].reset_index(drop=True), prefix)
    assert pd.isna(full.iloc[40].factor)


def test_downside_and_intraday_minimal_hand_calculation():
    days = pd.bdate_range("2025-01-02", periods=25)
    frame = pd.DataFrame({"trade_date": days, "symbol": "A", "open": 10., "close": 11., "adj_open": 10., "adj_close": 11.})
    assert compute_factor(frame, "intraday_momentum_20").iloc[-1].factor == pytest.approx(.1)
    assert compute_factor(frame, "downside_volatility_20").iloc[-1].factor == 0
    assert compute_factor(frame, "overnight_reversal_5").iloc[-1].factor == pytest.approx(1/11)
