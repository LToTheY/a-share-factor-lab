"""Extreme but valid cash and exchange-boundary cases must not fabricate fills."""

import pandas as pd
import pytest

from quant_lab.backtest.engine import (
    BacktestConfig,
    _commission,
    affordable_quantity,
    run_backtest,
)


def test_tiny_sale_cannot_make_cash_negative_through_minimum_fee():
    dates = pd.bdate_range("2025-01-02", periods=3)
    market = pd.DataFrame({"trade_date": dates, "symbol": "000001.SZ",
                           "open": [.1, .1, .01], "close": [.1, .1, .01]})
    targets = pd.DataFrame({"trade_date": dates[:2], "symbol": "000001.SZ", "target_weight": [1., 0.]})
    config = BacktestConfig(initial_cash=15.1, commission_rate=0, transfer_fee_rate=0,
                            stamp_duty_rate=0, historical_stamp_duty_rate=0, slippage_bps=0, minimum_commission=5)
    result = run_backtest(market, targets, config)
    assert result.equity.cash.min() >= 0
    assert result.trades.side.tolist() == ["BUY"]
    assert result.positions.shares.iloc[-1] == 100
    assert any("费用" in reason for reason in result.execution_issues.reason)


def test_slippage_price_stays_inside_known_exchange_limits():
    dates = pd.bdate_range("2025-01-02", periods=3)
    market = pd.DataFrame({"trade_date": dates, "symbol": "000001.SZ", "open": [10., 10.99, 9.01],
                           "close": [10., 10., 9.01], "up_limit": [11., 11., 11.], "down_limit": [9., 9., 9.],
                           "is_limit_up": False, "is_limit_down": False, "limit_status_known": True})
    targets = pd.DataFrame({"trade_date": dates[:2], "symbol": "000001.SZ", "target_weight": [1., 0.]})
    result = run_backtest(market, targets, BacktestConfig(initial_cash=10000, slippage_bps=50))
    assert result.trades.side.tolist() == ["BUY", "SELL"]
    assert result.trades.price.iloc[0] == pytest.approx(11.)
    assert result.trades.price.iloc[1] == pytest.approx(9.)


def test_transfer_fee_date_boundary_and_buy_budget_agree():
    config = BacktestConfig(minimum_commission=0, commission_rate=0)
    assert _commission(10000, config, "2022-04-28") == pytest.approx(.2)
    assert _commission(10000, config, "2022-04-29") == pytest.approx(.1)
    assert affordable_quantity(100, 100, 10000.15, "000001.SZ", config, "2022-04-28") == 0
    assert affordable_quantity(100, 100, 10000.15, "000001.SZ", config, "2022-04-29") == 100
    zero = BacktestConfig(transfer_fee_rate=0, minimum_commission=0, commission_rate=0)
    assert _commission(10000, zero, "2020-01-02") == 0


def test_missing_factor_price_is_not_silently_forward_filled():
    from quant_lab.factors.library import reversal_5

    frame = pd.DataFrame({"symbol": "A", "close": [10., float("nan"), 12., 13., 14., 15., 16.]})
    result = reversal_5(frame)
    assert result.iloc[5] == pytest.approx(-.5)
    assert pd.isna(result.iloc[6])


def test_inconsistent_open_is_not_repaired_into_a_fill():
    dates = pd.bdate_range("2025-01-02", periods=2)
    market = pd.DataFrame({"trade_date": dates, "symbol": "000001.SZ", "open": [10., 12.],
                           "close": [10., 12.], "up_limit": 11., "down_limit": 9., "limit_status_known": True})
    targets = pd.DataFrame({"trade_date": [dates[0]], "symbol": "000001.SZ", "target_weight": 1.})
    result = run_backtest(market, targets)
    assert result.trades.empty
    assert "行情不一致" in result.execution_issues.reason.iloc[0]


@pytest.mark.parametrize("flag", ["is_suspended", "is_limit_up", "is_limit_down"])
def test_nullable_trade_flags_block_fills_even_without_known_columns(flag):
    dates = pd.bdate_range("2025-01-02", periods=2)
    market = pd.DataFrame({"trade_date": dates, "symbol": "000001.SZ", "open": 10., "close": 10.,
                           flag: pd.Series([False, pd.NA], dtype="boolean")})
    targets = pd.DataFrame({"trade_date": [dates[0]], "symbol": "000001.SZ", "target_weight": 1.})
    result = run_backtest(market, targets)
    assert result.trades.empty
    assert "状态未知" in result.execution_issues.reason.iloc[0]


@pytest.mark.parametrize("values", [[100., float("nan"), 90.], [100., -1.], [0., 100.], [100., 0., 10.]])
def test_metrics_reject_invalid_or_unmodeled_equity(values):
    from quant_lab.backtest.metrics import performance_metrics

    with pytest.raises(ValueError):
        performance_metrics(pd.DataFrame({"equity": values}))


def test_zero_terminal_equity_and_chronology_are_explicit():
    from quant_lab.backtest.metrics import performance_metrics

    frame = pd.DataFrame({"equity": [100., 0., 0.]})
    result = performance_metrics(frame)
    assert result["total_return"] == -1. and result["annual_return"] == -1.
    assert result["max_drawdown"] == -1.
    frame["trade_date"] = pd.to_datetime(["2025-01-02", "2025-01-06", "2025-01-03"])
    with pytest.raises(ValueError, match="increasing"):
        performance_metrics(frame)
