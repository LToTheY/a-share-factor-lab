"""Independent accounting checks for saved backtest outputs."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def audit_ledger(equity, trades, positions, initial_cash: float) -> dict:
    """Audit a zero-initial-position account with no external cash flows.

    This uses saved executions only. Passing cannot establish that market data,
    dividends, tax assumptions or the original strategy were economically valid.
    """
    if isinstance(initial_cash, bool) or not math.isfinite(initial_cash) or initial_cash <= 0:
        raise ValueError("initial_cash must be positive and finite")
    equity, trades, positions = (frame.copy() for frame in (equity, trades, positions))
    for frame, columns in (
        (equity, ["trade_date", "cash", "market_value", "equity"]),
        (trades, ["trade_date", "signal_date", "symbol", "side", "shares", "price", "gross", "fee", "tax"]),
        (positions, ["trade_date", "symbol", "shares", "close", "market_value"]),
    ):
        missing = set(columns) - set(frame.columns)
        if missing:
            raise ValueError(f"Missing ledger columns: {sorted(missing)}")
        frame["trade_date"] = pd.to_datetime(frame.trade_date, errors="raise")
        if frame.trade_date.isna().any():
            raise ValueError("Ledger dates cannot be missing")
    if equity.empty or equity.trade_date.duplicated().any() or positions.duplicated(["trade_date", "symbol"]).any():
        raise ValueError("Equity must be nonempty; equity/position primary keys must be unique")
    equity = equity.sort_values("trade_date").set_index("trade_date")
    dates = equity.index
    checks = {}

    def close_check(name, actual, expected):
        actual, expected = np.asarray(actual, dtype=float), np.asarray(expected, dtype=float)
        differences = np.abs(actual - expected)
        checks[name] = {"passed": bool(np.isclose(actual, expected, rtol=1e-9, atol=1e-6).all()),
                        "maximum_absolute_error": float(differences.max()) if differences.size and np.isfinite(differences).all() else (0.0 if not differences.size else None)}

    for label, frame, fields in (
        ("equity", equity, ["cash", "market_value", "equity"]),
        ("trades", trades, ["shares", "price", "gross", "fee", "tax"]),
        ("positions", positions, ["shares", "close", "market_value"]),
    ):
        values = frame[fields].to_numpy(dtype=float)
        checks[label + "_finite_nonnegative"] = {"passed": bool(np.isfinite(values).all() and (values >= -1e-8).all())}
    checks["positive_integer_shares"] = {"passed": all(
        bool((frame.shares > 0).all() and frame.shares.eq(np.floor(frame.shares)).all()) for frame in (trades, positions))}
    checks["valid_trade_sides"] = {"passed": bool(trades.side.isin(["BUY", "SELL"]).all())}
    checks["positive_trade_prices"] = {"passed": bool(trades.price.gt(0).all())}
    checks["dates_within_equity_calendar"] = {"passed": bool(trades.trade_date.isin(dates).all() and positions.trade_date.isin(dates).all())}
    close_check("gross_equals_price_times_shares", trades.gross, trades.price * trades.shares)
    close_check("position_values_equal_marks_times_shares", positions.market_value, positions.close * positions.shares)
    close_check("equity_equals_cash_plus_market_value", equity.equity, equity.cash + equity.market_value)
    daily_market_value = positions.groupby("trade_date").market_value.sum().reindex(dates, fill_value=0)
    close_check("daily_position_values_match_equity", equity.market_value, daily_market_value)

    cash_flow = trades.gross.where(trades.side.eq("SELL"), -trades.gross) - trades.fee - trades.tax
    expected_cash = initial_cash + cash_flow.groupby(trades.trade_date).sum().reindex(dates, fill_value=0).cumsum()
    close_check("cash_reconciles_from_executions", equity.cash, expected_cash)
    trade_shares = trades.assign(net_shares=trades.shares.where(trades.side.eq("BUY"), -trades.shares))
    symbols = sorted(set(trades.symbol) | set(positions.symbol))
    changes = trade_shares.pivot_table(index="trade_date", columns="symbol", values="net_shares", aggfunc="sum").reindex(index=dates, columns=symbols).fillna(0)
    actual = positions.pivot(index="trade_date", columns="symbol", values="shares").reindex(index=dates, columns=symbols).fillna(0)
    close_check("holdings_reconcile_from_executions", actual.to_numpy(), changes.cumsum().to_numpy())
    checks["never_short_from_executions"] = {"passed": bool(changes.cumsum().ge(0).all(axis=None))}

    signals = pd.to_datetime(trades.signal_date, errors="raise")
    next_slots = dates.searchsorted(signals, side="right")
    valid_slots = next_slots < len(dates)
    next_dates = dates.take(np.minimum(next_slots, len(dates) - 1))
    checks["execution_on_next_observed_session"] = {"passed": bool(
        signals.notna().all() and signals.isin(dates).all() and valid_slots.all()
        and np.array_equal(next_dates.to_numpy(), trades.trade_date.to_numpy()))}
    fees = float(trades[["fee", "tax"]].sum().sum())
    net_profit = float(equity.equity.iloc[-1] - initial_cash)
    gross_profit = net_profit + fees
    fees = fees if math.isfinite(fees) else None
    net_profit = net_profit if math.isfinite(net_profit) else None
    gross_profit = gross_profit if math.isfinite(gross_profit) else None
    return {"passed": all(item["passed"] for item in checks.values()), "sessions": len(equity),
            "trade_count": len(trades), "checks": checks,
            "net_profit": net_profit, "explicit_fees_and_taxes": fees,
            "net_profit_plus_explicit_costs": gross_profit,
            "limitations": "Saved-fill reconciliation only; not independent market-price validation. Adding fees back holds fills fixed and is not a zero-cost strategy backtest. Corporate actions and external cash flows are not reconstructed."}
