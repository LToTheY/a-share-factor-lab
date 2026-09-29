"""Observable causality, sorting and stock-boundary checks for price-volume factors."""

import numpy as np
import pandas as pd
import pytest

from quant_lab.data.synthetic import make_synthetic_daily_data
from quant_lab.factors.library import compute_factor

PRICE_VOLUME_FACTORS = [
    "momentum_20_5", "momentum_60_5", "momentum_120_20", "reversal_5", "reversal_20",
    "amihud_20", "volatility_20", "volatility_60", "downside_volatility_20",
    "overnight_reversal_5", "intraday_momentum_20", "turnover_mean_20",
    "amount_momentum_20", "price_volume_corr_20", "ivol_60",
]


@pytest.fixture(scope="module")
def panel():
    frame = make_synthetic_daily_data(5, 260, seed=317)
    for field in ("open", "high", "low", "close"):
        frame[f"adj_{field}"] = frame[field] * frame.adj_factor
    frame["turnover_rate"] = frame.volume / 1e8
    frame["in_universe"] = True
    frame["research_segment"] = 0
    return frame


@pytest.mark.parametrize("name", PRICE_VOLUME_FACTORS)
def test_price_volume_factors_use_only_past_rows_and_preserve_stock_boundaries(panel, name):
    original = panel.copy(deep=True)
    full = compute_factor(panel, name)
    assert full.factor.notna().sum() > 0
    assert not np.isinf(full.factor.to_numpy()).any()
    pd.testing.assert_frame_equal(panel, original)
    shuffled = compute_factor(panel.sample(frac=1, random_state=11), name)
    pd.testing.assert_frame_equal(full, shuffled)

    cutoff = panel.trade_date.sort_values().unique()[179]
    prefix = compute_factor(panel.loc[panel.trade_date.le(cutoff)], name)
    pd.testing.assert_frame_equal(prefix, full.loc[full.trade_date.le(cutoff)].reset_index(drop=True))
    changed = panel.copy()
    future = changed.trade_date.gt(cutoff)
    fields = ["open", "high", "low", "close", "adj_open", "adj_high", "adj_low", "adj_close", "amount", "volume", "turnover_rate"]
    changed.loc[future, fields] *= 7
    changed_result = compute_factor(changed, name)
    pd.testing.assert_frame_equal(prefix, changed_result.loc[changed_result.trade_date.le(cutoff)].reset_index(drop=True))

    # All these formulas are per-stock except IVOL, which deliberately estimates
    # an equal-weight market return from the contemporaneous cross section.
    if name != "ivol_60":
        symbol = panel.symbol.iloc[0]
        single = compute_factor(panel.loc[panel.symbol.eq(symbol)], name)
        pd.testing.assert_frame_equal(single, full.loc[full.symbol.eq(symbol)].reset_index(drop=True))

    segmented = panel.copy()
    segmented.loc[future, "research_segment"] = 1
    restarted = compute_factor(segmented, name)
    first_new_session = panel.loc[future, "trade_date"].min()
    assert restarted.loc[restarted.trade_date.eq(first_new_session), "factor"].isna().all()
