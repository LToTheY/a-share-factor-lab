import numpy as np
import pandas as pd
import pytest

from scripts.audit_corporate_actions import normalize_events, reconcile_prices


def sample_events():
    return pd.DataFrame({"stkcd": [1, 1], "disttyp": ["CA", "SD"],
                         "exdistdt": ["2024-06-04"] * 2, "annodt": [20240520] * 2,
                         "paydt": [20240604] * 2, "amount": ["0.6", "0.2"], "roprc": [None] * 2})


def sample_prices():
    return pd.DataFrame({"symbol": ["000001.SZ"] * 2, "trade_date": pd.to_datetime(["2024-06-03", "2024-06-04"]),
                         "close": [12.6, 10.1], "preclose": [12.5, 10.]})


def test_per_share_cash_and_additional_share_ratio():
    events = normalize_events(sample_events())
    assert events.supported_for_gross_ledger.all()
    result = reconcile_prices(events, sample_prices()).iloc[0]
    assert result.expected_preclose == pytest.approx(10.)
    assert result.status == "MATCH_WITHIN_TICK_TOLERANCE"


@pytest.mark.parametrize("column,value,reason", [("paydt", 0, "unknown_payment_date"),
    ("annodt", 20240605, "announcement_after_ex_date"), ("disttyp", "RO", "unsupported_kind"),
    ("amount", "inf", "invalid_amount"), ("paydt", 20240603, "payment_before_ex_date")])
def test_uncertain_event_cannot_enter_supported_ledger(column, value, reason):
    raw = sample_events()
    raw.loc[0, column] = value
    events = normalize_events(raw)
    invalid = events.loc[~events.supported_for_gross_ledger]
    assert len(invalid) == 1 and reason in invalid.iloc[0].issues
    assert reconcile_prices(events, sample_prices()).iloc[0].status == "NOT_COMPARABLE"


def test_duplicate_distribution_rejected_not_summed():
    raw = sample_events()
    events = normalize_events(pd.concat([raw, raw.iloc[[0]]], ignore_index=True))
    assert events.issues.str.contains("duplicate_event").sum() == 2
    assert reconcile_prices(events, sample_prices()).iloc[0].status == "NOT_COMPARABLE"


def test_missing_intermediate_session_cannot_supply_prior_close():
    prices = sample_prices()
    prices.loc[0, "trade_date"] = pd.Timestamp("2024-05-31")
    extra = {"symbol": "600000.SH", "trade_date": pd.Timestamp("2024-06-03"), "close": 9., "preclose": 9.}
    result = reconcile_prices(normalize_events(sample_events()), pd.concat([prices, pd.DataFrame([extra])])).iloc[0]
    assert np.isnan(result.previous_close)
    assert result.status == "NOT_COMPARABLE"


def test_nullable_market_values_stay_unverified():
    prices = sample_prices()
    prices["preclose"] = prices.preclose.astype("Float64")
    prices.loc[1, "preclose"] = pd.NA
    result = reconcile_prices(normalize_events(sample_events()), prices).iloc[0]
    assert result.status == "NOT_COMPARABLE"
