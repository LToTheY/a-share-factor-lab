"""Behavioral checks for signal-time selection and a self-financing ledger."""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from quant_lab.backtest.engine import BacktestConfig, affordable_quantity, run_backtest
from quant_lab.strategy.sandbox import StrategySpec, run_strategy


def panel(days=6):
    rows, scores = [], []
    for date in pd.bdate_range("2025-01-02", periods=days):
        for symbol, price, alpha in [("600001.SH", 100., 3.), ("000001.SZ", 9., 2.), ("300001.SZ", 5., 1.)]:
            rows.append({"trade_date": date, "symbol": symbol, "open": price, "close": price, "adj_close": price, "in_index": True})
            scores.append({"trade_date": date, "symbol": symbol, "in_universe": True, "alpha": alpha})
    return pd.DataFrame(scores), pd.DataFrame(rows)


def spec(**kwargs):
    return StrategySpec({"alpha": 1.}, top_n=1, exit_rank=2, max_weight=1.,
                        rebalance_frequency="D", selection_mode="affordable", **kwargs)


def test_affordable_candidates_use_close_budget_and_preserve_skips():
    scores, market = panel()
    result = run_strategy(scores, market, spec(allow_chinext=False), BacktestConfig(initial_cash=3000))
    assert set(result.targets.symbol) == {"000001.SZ"}
    assert set(result.backtest.trades.symbol) == {"000001.SZ"}
    first = result.selection_audit.query("trade_date == trade_date.min()")
    assert "预算不足" in first.loc[first.symbol.eq("600001.SH"), "reason"].iloc[0]
    assert "未启用" in first.loc[first.symbol.eq("300001.SZ"), "reason"].iloc[0]
    assert result.diagnostics["average_cash_ratio"] < .5


def test_no_affordable_candidates_is_valid_cash_result():
    scores, market = panel()
    result = run_strategy(scores, market, spec(), BacktestConfig(initial_cash=100))
    assert result.backtest.trades.empty
    assert result.targets.empty
    assert result.diagnostics["average_holdings"] == 0
    assert result.diagnostics["average_cash_ratio"] == 1
    assert result.metrics["total_return"] == 0


def test_buffer_uses_executed_holdings_not_failed_targets():
    scores, market = panel()
    dates = sorted(scores.trade_date.unique())
    # The top stock cannot fill on day two. At that close B becomes rank one.
    market["is_limit_up"] = market.symbol.eq("600001.SH") & market.trade_date.eq(dates[1])
    scores.loc[(scores.trade_date >= dates[1]) & scores.symbol.eq("000001.SZ"), "alpha"] = 4.
    result = run_strategy(scores, market, replace(spec(), selection_mode="rank"), BacktestConfig(initial_cash=30000))
    day_two = result.targets[result.targets.trade_date.eq(dates[1])]
    assert set(day_two.symbol) == {"000001.SZ"}


def test_future_price_changes_do_not_change_prior_targets_or_trades():
    scores, market = panel(10)
    before = run_strategy(scores, market, spec(), BacktestConfig(initial_cash=3000))
    boundary = sorted(market.trade_date.unique())[6]
    altered = market.copy()
    altered.loc[altered.trade_date >= boundary, ["open", "close", "adj_close"]] *= 10
    after = run_strategy(scores, altered, spec(), BacktestConfig(initial_cash=3000))
    for left, right in [(before.targets, after.targets), (before.backtest.trades, after.backtest.trades)]:
        pd.testing.assert_frame_equal(left[left.trade_date < boundary].reset_index(drop=True), right[right.trade_date < boundary].reset_index(drop=True))


def test_missing_future_close_does_not_prevent_open_execution():
    dates = pd.bdate_range("2025-01-02", periods=3)
    market = pd.DataFrame({"trade_date": dates, "symbol": "A", "open": 10., "close": [10., np.nan, 11.]})
    targets = pd.DataFrame({"trade_date": [dates[0]], "symbol": "A", "target_weight": 1.})
    result = run_backtest(market, targets, BacktestConfig(initial_cash=3000))
    assert len(result.trades) == 1
    assert result.trades.iloc[0].trade_date == dates[1]
    assert len(result.execution_issues.query("side == 'VALUATION'")) == 1
    assert result.equity.iloc[1].market_value == 2000


@pytest.mark.parametrize("kwargs", [{"initial_cash": float("nan")}, {"commission_rate": float("inf")}, {"slippage_bps": 10000}, {"lot_size": 1.5}])
def test_invalid_ledger_parameters_fail_early(kwargs):
    with pytest.raises(ValueError):
        BacktestConfig(**kwargs)


def test_budget_solver_matches_exhaustive_valid_quantities():
    config = BacktestConfig()
    rng = np.random.default_rng(52)
    for symbol, unit in [("600001.SH", 100), ("688001.SH", 1)]:
        for _ in range(30):
            price = float(rng.uniform(1, 80))
            cash = float(rng.uniform(100, 20000))
            maximum = 1000
            quantities = [q for q in range(200 if unit == 1 else 100, maximum + 1, unit)
                          if q * price + max(config.minimum_commission, q * price * config.commission_rate)
                          + q * price * config.transfer_fee_rate <= cash]
            assert affordable_quantity(maximum, price, cash, symbol, config) == max(quantities, default=0)


def test_cash_and_shares_reconcile_on_every_date():
    scores, market = panel(8)
    dates = sorted(scores.trade_date.unique())
    scores.loc[(scores.trade_date >= dates[3]) & scores.symbol.eq("300001.SZ"), "alpha"] = 4
    result = run_strategy(scores, market, spec(), BacktestConfig(initial_cash=3000))
    cash, shares = 3000., {}
    for row in result.backtest.equity.itertuples(index=False):
        trades = result.backtest.trades[result.backtest.trades.trade_date.eq(row.trade_date)]
        for trade in trades.itertuples(index=False):
            sign = 1 if trade.side == "BUY" else -1
            cash -= sign * trade.gross + trade.fee + trade.tax
            shares[trade.symbol] = shares.get(trade.symbol, 0) + sign * trade.shares
        held = result.backtest.positions[result.backtest.positions.trade_date.eq(row.trade_date)]
        assert cash == pytest.approx(row.cash)
        assert row.cash >= 0
        assert row.equity == pytest.approx(cash + held.market_value.sum())
        assert {s: q for s, q in shares.items() if q} == held.set_index("symbol").shares.to_dict()


def test_insufficient_cash_prefers_signal_rank_not_symbol_alphabet():
    dates = pd.bdate_range("2025-01-02", periods=2)
    market = pd.DataFrame([{"trade_date": date, "symbol": s, "open": 10., "close": 10.} for date in dates for s in "ABC"])
    targets = pd.DataFrame({"trade_date": dates[0], "symbol": list("ABC"), "target_weight": 1/3, "factor_rank": [3, 1, 2]})
    result = run_backtest(market, targets, BacktestConfig(initial_cash=3000, slippage_bps=0))
    assert result.trades.symbol.to_list() == ["B", "C"]


def test_explicit_cash_instruction_liquidates_but_missing_signal_holds():
    scores, market = panel()
    cutoff = sorted(scores.trade_date.unique())[2]
    missing = scores.copy()
    missing.loc[missing.trade_date >= cutoff, "alpha"] = np.nan
    held = run_strategy(missing, market, spec(), BacktestConfig(initial_cash=3000))
    assert not held.backtest.positions[held.backtest.positions.trade_date == market.trade_date.max()].empty
    missing["force_cash"] = missing.trade_date >= cutoff
    cleared = run_strategy(missing, market, spec(), BacktestConfig(initial_cash=3000))
    assert cleared.backtest.positions[cleared.backtest.positions.trade_date == market.trade_date.max()].empty
    sells = cleared.backtest.trades.query("side == 'SELL'")
    assert (sells.trade_date > cutoff).all()


def test_corporate_action_warning_requires_prior_held_position():
    dates = pd.bdate_range("2025-01-02", periods=3)
    market = pd.DataFrame({"trade_date": dates, "symbol": "A", "open": [10., 10., 5.], "close": [10., 10., 5.], "preclose": [10., 10., 5.]})
    weights = pd.DataFrame({"trade_date": [dates[0]], "symbol": "A", "target_weight": 1.})
    result = run_backtest(market, weights, BacktestConfig(initial_cash=3000))
    issue = result.execution_issues.query("side == 'CORPORATE_ACTION'")
    assert len(issue) == 1
    assert issue.iloc[0].trade_date == dates[2]
    assert issue.iloc[0].previous_close == 10 and issue.iloc[0].exchange_preclose == 5


def test_long_backtest_has_cancellation_checkpoints():
    scores, market = panel(70)
    calls = []
    def stop(message):
        calls.append(message)
        if len(calls) == 2:
            raise RuntimeError("cancelled by test")
    with pytest.raises(RuntimeError, match="cancelled"):
        run_strategy(scores, market, spec(), BacktestConfig(initial_cash=3000), progress=stop)
    assert len(calls) == 2
