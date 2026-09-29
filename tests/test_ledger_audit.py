"""A separately calculated audit must detect corruption of saved executions."""

import json

import pandas as pd
import pytest

from quant_lab.backtest.audit import audit_ledger


def ledger():
    days = pd.bdate_range("2025-01-02", periods=3)
    equity = pd.DataFrame({"trade_date": days, "cash": [2000., 995., 2090.], "market_value": [0., 1050., 0.], "equity": [2000., 2045., 2090.]})
    trades = pd.DataFrame({"trade_date": days[1:], "signal_date": days[:2], "symbol": "600001.SH",
                           "side": ["BUY", "SELL"], "shares": 100, "price": [10., 11.],
                           "gross": [1000., 1100.], "fee": 5., "tax": 0.})
    positions = pd.DataFrame({"trade_date": [days[1]], "symbol": "600001.SH", "shares": 100, "close": 10.5, "market_value": 1050.})
    return equity, trades, positions


def test_hand_calculated_ledger_and_cost_bridge():
    result = audit_ledger(*ledger(), 2000.)
    assert result["passed"]
    assert result["net_profit"] == 90.
    assert result["explicit_fees_and_taxes"] == 10.
    assert result["net_profit_plus_explicit_costs"] == 100.


@pytest.mark.parametrize("field,changed,check", [
    ("fee", 6., "cash_reconciles_from_executions"),
    ("gross", 999., "gross_equals_price_times_shares"),
    ("shares", 99, "holdings_reconcile_from_executions"),
    ("signal_date", pd.Timestamp("2025-01-03"), "execution_on_next_observed_session"),
])
def test_modified_fill_fails_independent_reconciliation(field, changed, check):
    equity, trades, positions = ledger()
    trades.loc[0, field] = changed
    result = audit_ledger(equity, trades, positions, 2000.)
    assert not result["passed"] and not result["checks"][check]["passed"]


def test_missing_position_or_false_mark_is_detected():
    equity, trades, positions = ledger()
    assert not audit_ledger(equity, trades, positions.iloc[:0], 2000.)["passed"]
    positions.loc[0, "close"] = 12.
    assert not audit_ledger(equity, trades, positions, 2000.)["checks"]["position_values_equal_marks_times_shares"]["passed"]


def test_empty_cash_only_strategy_passes_without_fabricated_trades():
    equity, trades, positions = ledger()
    equity["cash"], equity["market_value"], equity["equity"] = 2000., 0., 2000.
    assert audit_ledger(equity, trades.iloc[:0], positions.iloc[:0], 2000.)["passed"]


def test_invalid_ending_equity_remains_a_serializable_failure_record():
    equity, trades, positions = ledger()
    equity.loc[2, "equity"] = float("nan")
    result = audit_ledger(equity, trades, positions, 2000.)
    assert not result["passed"] and result["net_profit"] is None
    json.dumps(result, allow_nan=False)


def test_signal_on_unobserved_weekend_is_rejected():
    equity, trades, positions = ledger()
    trades.loc[1, "signal_date"] = pd.Timestamp("2025-01-04")
    result = audit_ledger(equity, trades, positions, 2000.)
    assert not result["checks"]["execution_on_next_observed_session"]["passed"]


def test_zero_execution_price_is_rejected():
    equity, trades, positions = ledger()
    trades.loc[0, "price"] = 0.
    assert not audit_ledger(equity, trades, positions, 2000.)["checks"]["positive_trade_prices"]["passed"]
