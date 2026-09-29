"""Transparent daily ledger with next-session execution and A-share constraints."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from quant_lab.backtest.lot_rules import order_unit, round_order
from quant_lab.data.schema import require_columns


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: float = 1_000_000.0
    commission_rate: float = 0.0003
    stamp_duty_rate: float = 0.0005
    historical_stamp_duty_rate: float = 0.001
    stamp_duty_change_date: str = "2023-08-28"
    transfer_fee_rate: float = 0.00001
    slippage_bps: float = 5.0
    minimum_commission: float = 5.0
    lot_size: int = 100

    def __post_init__(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        nonnegative = {
            "commission_rate": self.commission_rate,
            "stamp_duty_rate": self.stamp_duty_rate,
            "historical_stamp_duty_rate": self.historical_stamp_duty_rate,
            "transfer_fee_rate": self.transfer_fee_rate,
            "slippage_bps": self.slippage_bps,
            "minimum_commission": self.minimum_commission,
        }
        invalid = [name for name, value in nonnegative.items() if value < 0]
        if invalid:
            raise ValueError(f"Backtest costs cannot be negative: {', '.join(invalid)}")
        if self.lot_size <= 0:
            raise ValueError("lot_size must be positive")


@dataclass
class BacktestResult:
    equity: pd.DataFrame
    trades: pd.DataFrame
    positions: pd.DataFrame
    execution_issues: pd.DataFrame = field(default_factory=pd.DataFrame)


def _commission(gross: float, config: BacktestConfig) -> float:
    commission = max(config.minimum_commission, gross * config.commission_rate)
    return commission + gross * config.transfer_fee_rate


def _stamp_duty_rate(trade_date: pd.Timestamp, config: BacktestConfig) -> float:
    """Return the sell-side tax rate that was observable on the trade date."""
    change_date = pd.Timestamp(config.stamp_duty_change_date)
    if pd.Timestamp(trade_date) < change_date:
        return config.historical_stamp_duty_rate
    return config.stamp_duty_rate


def _execution_schedule(
    signal_dates: pd.Series,
    trading_dates: pd.DatetimeIndex,
) -> dict[pd.Timestamp, pd.Timestamp]:
    """Map signal date to the next strictly later market session."""
    schedule: dict[pd.Timestamp, pd.Timestamp] = {}
    for raw_date in pd.to_datetime(signal_dates).sort_values().unique():
        signal_date = pd.Timestamp(raw_date)
        position = trading_dates.searchsorted(signal_date, side="right")
        if position < len(trading_dates):
            schedule[trading_dates[position]] = signal_date
    return schedule


def _data_issue(row: pd.Series) -> str | None:
    if "is_usable_market_data" in row and not bool(row["is_usable_market_data"]):
        return "行情质量异常，禁止模拟成交"
    for column in ["is_st_known", "is_suspended_known", "limit_status_known"]:
        if column in row and (pd.isna(row[column]) or not bool(row[column])):
            return "交易状态未知，禁止模拟成交"
    if not np.isfinite(row["open"]) or row["open"] <= 0:
        return "开盘价无效"
    if not np.isfinite(row["close"]) or row["close"] <= 0:
        return "收盘估值价格无效"
    return None


def run_backtest(
    market: pd.DataFrame,
    target_weights: pd.DataFrame,
    config: BacktestConfig | None = None,
) -> BacktestResult:
    """Execute dated targets at the next session open and mark at daily close."""
    config = config or BacktestConfig()
    require_columns(market, ["trade_date", "symbol", "open", "close"])
    require_columns(target_weights, ["trade_date", "symbol", "target_weight"])
    market = market.copy()
    market["trade_date"] = pd.to_datetime(market["trade_date"])
    target_weights = target_weights.copy()
    target_weights["trade_date"] = pd.to_datetime(target_weights["trade_date"])
    if market.duplicated(["trade_date", "symbol"]).any():
        raise ValueError("Market contains duplicate trade_date/symbol rows")
    if target_weights.duplicated(["trade_date", "symbol"]).any():
        raise ValueError("Target weights contain duplicate trade_date/symbol rows")
    weights = pd.to_numeric(target_weights["target_weight"], errors="coerce")
    if weights.isna().any() or not np.isfinite(weights).all():
        raise ValueError("Target weights must be finite numbers")
    if (weights < 0).any():
        raise ValueError("Target weights cannot be negative")
    daily_weight = weights.groupby(target_weights["trade_date"]).sum()
    if (daily_weight > 1.0 + 1e-9).any():
        raise ValueError("Target weights cannot exceed 100% on any signal date")
    target_weights["target_weight"] = weights
    trading_dates = pd.DatetimeIndex(sorted(market["trade_date"].unique()))
    schedule = _execution_schedule(target_weights["trade_date"], trading_dates)

    cash = float(config.initial_cash)
    shares: dict[str, int] = {}
    last_marks: dict[str, float] = {}
    equity_rows = []
    trade_rows = []
    position_rows = []
    issue_rows = []
    slippage = config.slippage_bps / 10_000.0

    for trade_date, day in market.groupby("trade_date", sort=True):
        day = day.set_index("symbol", drop=False)
        if trade_date in schedule:
            signal_date = schedule[trade_date]
            targets = target_weights[target_weights["trade_date"] == signal_date]
            target_map = dict(zip(targets["symbol"], targets["target_weight"]))

            def record_issue(
                symbol,
                side,
                reason,
                requested=None,
                unfilled=None,
                trade_date=trade_date,
                signal_date=signal_date,
            ):
                issue_rows.append(
                    {
                        "trade_date": trade_date,
                        "signal_date": signal_date,
                        "symbol": symbol,
                        "side": side,
                        "reason": reason,
                        "requested_shares": requested,
                        "unfilled_shares": unfilled,
                    }
                )

            open_equity = cash + sum(
                quantity * (float(day.at[symbol, "open"])
                            if symbol in day.index and np.isfinite(day.at[symbol, "open"]) and day.at[symbol, "open"] > 0
                            else last_marks[symbol])
                for symbol, quantity in shares.items()
            )

            # Sell first so proceeds can finance purchases.
            for symbol in sorted(set(shares) | set(target_map)):
                if symbol not in day.index:
                    record_issue(symbol, "REBALANCE", "缺少当日行情")
                    continue
                row = day.loc[symbol]
                data_issue = _data_issue(row)
                if data_issue:
                    record_issue(symbol, "REBALANCE", data_issue)
                    continue
                desired_value = open_equity * target_map.get(symbol, 0.0)
                raw_price = float(row["open"])
                desired_shares = round_order(desired_value / raw_price, symbol, config.lot_size)
                current_shares = shares.get(symbol, 0)
                quantity = round_order(current_shares - desired_shares, symbol, config.lot_size, holding=current_shares)
                if 0 < current_shares - desired_shares and quantity == 0:
                    record_issue(symbol, "SELL", "调整数量不足板块最低申报量", current_shares - desired_shares, current_shares - desired_shares)
                blocked = bool(row.get("is_suspended", False)) or bool(
                    row.get("is_limit_down", False)
                )
                if quantity <= 0:
                    continue
                if blocked:
                    reason = (
                        "停牌"
                        if bool(row.get("is_suspended", False))
                        else "跌停禁止卖出"
                    )
                    record_issue(symbol, "SELL", reason, quantity, quantity)
                    continue
                price = raw_price * (1 - slippage)
                gross = quantity * price
                fee = _commission(gross, config)
                tax_rate = _stamp_duty_rate(trade_date, config)
                tax = gross * tax_rate
                cash += gross - fee - tax
                shares[symbol] = current_shares - quantity
                trade_rows.append(
                    {
                        "trade_date": trade_date,
                        "signal_date": signal_date,
                        "symbol": symbol,
                        "side": "SELL",
                        "shares": quantity,
                        "price": price,
                        "gross": gross,
                        "fee": fee,
                        "tax": tax,
                        "tax_rate": tax_rate,
                    }
                )

            for symbol, weight in sorted(target_map.items()):
                if symbol not in day.index:
                    continue
                row = day.loc[symbol]
                if _data_issue(row):
                    continue  # The sell-first loop already recorded the blocked rebalance.
                blocked = bool(row.get("is_suspended", False)) or bool(
                    row.get("is_limit_up", False)
                )
                raw_price = float(row["open"])
                desired_value = open_equity * weight
                desired_shares = round_order(desired_value / raw_price, symbol, config.lot_size)
                quantity = round_order(desired_shares - shares.get(symbol, 0), symbol, config.lot_size)
                if quantity <= 0:
                    if weight > 0 and (desired_shares > shares.get(symbol, 0) or desired_shares == 0):
                        record_issue(symbol, "BUY", "目标金额不足一手（板块最低申报量）", 0, 0)
                    continue
                if blocked:
                    reason = (
                        "停牌"
                        if bool(row.get("is_suspended", False))
                        else "涨停禁止买入"
                    )
                    record_issue(symbol, "BUY", reason, quantity, quantity)
                    continue
                requested_quantity = quantity
                price = raw_price * (1 + slippage)
                # Reduce by lots until cash covers price and commission.
                while quantity > 0:
                    gross = quantity * price
                    fee = _commission(gross, config)
                    if gross + fee <= cash:
                        break
                    minimum, step = order_unit(symbol, config.lot_size)
                    quantity -= step
                    if quantity < minimum:
                        quantity = 0
                if quantity < requested_quantity:
                    record_issue(
                        symbol,
                        "BUY",
                        "现金不足（含费用）",
                        requested_quantity,
                        requested_quantity - quantity,
                    )
                if quantity <= 0:
                    continue
                gross = quantity * price
                fee = _commission(gross, config)
                cash -= gross + fee
                shares[symbol] = shares.get(symbol, 0) + quantity
                trade_rows.append(
                    {
                        "trade_date": trade_date,
                        "signal_date": signal_date,
                        "symbol": symbol,
                        "side": "BUY",
                        "shares": quantity,
                        "price": price,
                        "gross": gross,
                        "fee": fee,
                        "tax": 0.0,
                        "tax_rate": 0.0,
                    }
                )

            shares = {
                symbol: quantity for symbol, quantity in shares.items() if quantity
            }

        market_value = 0.0
        for symbol, quantity in shares.items():
            stale = symbol not in day.index or not np.isfinite(day.at[symbol, "close"]) or day.at[symbol, "close"] <= 0
            if stale:
                price = last_marks[symbol]
                issue_rows.append({"trade_date": trade_date, "signal_date": pd.NaT,
                                   "symbol": symbol, "side": "VALUATION",
                                   "reason": "缺少有效收盘价，暂沿用上次估值；禁止视为精确收益",
                                   "requested_shares": None, "unfilled_shares": None})
            else:
                price = float(day.at[symbol, "close"])
                last_marks[symbol] = price
            value = quantity * price
            market_value += value
            position_rows.append(
                {
                    "trade_date": trade_date,
                    "symbol": symbol,
                    "shares": quantity,
                    "close": price,
                    "market_value": value,
                }
            )
        equity_rows.append(
            {
                "trade_date": trade_date,
                "cash": cash,
                "market_value": market_value,
                "equity": cash + market_value,
            }
        )

    trade_columns = [
        "trade_date",
        "signal_date",
        "symbol",
        "side",
        "shares",
        "price",
        "gross",
        "fee",
        "tax",
        "tax_rate",
    ]
    position_columns = ["trade_date", "symbol", "shares", "close", "market_value"]
    return BacktestResult(
        equity=pd.DataFrame(equity_rows),
        trades=pd.DataFrame(trade_rows, columns=trade_columns),
        positions=pd.DataFrame(position_rows, columns=position_columns),
        execution_issues=pd.DataFrame(
            issue_rows,
            columns=[
                "trade_date",
                "signal_date",
                "symbol",
                "side",
                "reason",
                "requested_shares",
                "unfilled_shares",
            ],
        ),
    )
