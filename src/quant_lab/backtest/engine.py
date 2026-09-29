"""Transparent daily ledger with next-session execution and A-share constraints."""

from __future__ import annotations

from collections.abc import Callable
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
    historical_transfer_fee_multiplier: float = 2.0
    transfer_fee_change_date: str = "2022-04-29"
    slippage_bps: float = 5.0
    minimum_commission: float = 5.0
    lot_size: int = 100

    def __post_init__(self) -> None:
        if not np.isfinite(self.initial_cash) or self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        nonnegative = {
            "commission_rate": self.commission_rate,
            "stamp_duty_rate": self.stamp_duty_rate,
            "historical_stamp_duty_rate": self.historical_stamp_duty_rate,
            "transfer_fee_rate": self.transfer_fee_rate,
            "historical_transfer_fee_multiplier": self.historical_transfer_fee_multiplier,
            "slippage_bps": self.slippage_bps,
            "minimum_commission": self.minimum_commission,
        }
        invalid = [name for name, value in nonnegative.items() if not np.isfinite(value) or value < 0]
        if invalid:
            raise ValueError(f"Backtest costs cannot be negative: {', '.join(invalid)}")
        if self.slippage_bps >= 10000:
            raise ValueError("slippage_bps must be below 10000")
        if not isinstance(self.lot_size, int) or isinstance(self.lot_size, bool) or self.lot_size <= 0:
            raise ValueError("lot_size must be positive")
        if pd.isna(pd.Timestamp(self.stamp_duty_change_date)):
            raise ValueError("Invalid stamp duty change date")
        if pd.isna(pd.Timestamp(self.transfer_fee_change_date)):
            raise ValueError("Invalid transfer fee change date")


@dataclass
class BacktestResult:
    equity: pd.DataFrame
    trades: pd.DataFrame
    positions: pd.DataFrame
    execution_issues: pd.DataFrame = field(default_factory=pd.DataFrame)
    targets: pd.DataFrame = field(default_factory=pd.DataFrame)


def _transfer_fee_rate(config: BacktestConfig, trade_date=None) -> float:
    historical = trade_date is not None and pd.Timestamp(trade_date) < pd.Timestamp(config.transfer_fee_change_date)
    return config.transfer_fee_rate * (config.historical_transfer_fee_multiplier if historical else 1.)


def _commission(gross: float, config: BacktestConfig, trade_date=None) -> float:
    commission = max(config.minimum_commission, gross * config.commission_rate)
    return commission + gross * _transfer_fee_rate(config, trade_date)


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
    for column in ("is_suspended", "is_limit_up", "is_limit_down"):
        if column in row and pd.isna(row[column]):
            return "交易状态未知，禁止模拟成交"
    if "is_usable_market_data" in row and (pd.isna(row["is_usable_market_data"]) or not bool(row["is_usable_market_data"])):
        return "行情质量异常，禁止模拟成交"
    for column in ["is_st_known", "is_suspended_known", "limit_status_known"]:
        if column in row and (pd.isna(row[column]) or not bool(row[column])):
            return "交易状态未知，禁止模拟成交"
    if not np.isfinite(row["open"]) or row["open"] <= 0:
        return "开盘价无效"
    up, down = row.get("up_limit", np.nan), row.get("down_limit", np.nan)
    if pd.notna(up) and np.isfinite(up) and up > 0 and row["open"] > up + .0051:
        return "开盘价超过已知涨停价，行情不一致"
    if pd.notna(down) and np.isfinite(down) and down > 0 and row["open"] < down - .0051:
        return "开盘价低于已知跌停价，行情不一致"
    return None


def affordable_quantity(quantity: int, price: float, cash: float, symbol: str,
                        config: BacktestConfig, trade_date=None) -> int:
    """Solve the fee-inclusive budget directly, then apply the order minimum."""
    if cash <= config.minimum_commission or price <= 0 or not np.isfinite(price):
        return 0
    transfer_rate = _transfer_fee_rate(config, trade_date)
    bound = min(cash / (price * (1 + config.commission_rate + transfer_rate)),
                (cash - config.minimum_commission) / (price * (1 + transfer_rate)))
    candidate = round_order(min(quantity, bound), symbol, config.lot_size)
    if candidate and candidate * price + _commission(candidate * price, config, trade_date) > cash + 1e-9:
        _, step = order_unit(symbol, config.lot_size)
        candidate = round_order(candidate - step, symbol, config.lot_size)
    return candidate


def _execution_price(row: pd.Series, side: str, slippage: float) -> float:
    """Apply adverse slippage within known exchange bands, never future OHLC."""
    price = float(row["open"]) * (1 + slippage if side == "BUY" else 1 - slippage)
    for column, clamp in (("up_limit", min), ("down_limit", max)):
        bound = row.get(column, np.nan)
        if pd.notna(bound) and np.isfinite(bound) and bound > 0:
            price = clamp(price, float(bound))
    return price


def run_backtest(
    market: pd.DataFrame,
    target_weights: pd.DataFrame,
    config: BacktestConfig | None = None,
    *,
    target_builder: Callable | None = None,
    progress: Callable | None = None,
) -> BacktestResult:
    """Execute next-open targets; an optional callback sees only each day's close.

    The callback receives (date, day, cash, held_shares, last_marks), returns a
    target frame or None (no rebalance). An empty frame explicitly means cash.
    It cannot see subsequent prices through these arguments.
    """
    config = config or BacktestConfig()
    require_columns(market, ["trade_date", "symbol", "open", "close"])
    require_columns(target_weights, ["trade_date", "symbol", "target_weight"])
    market = market.copy()
    market["trade_date"] = pd.to_datetime(market["trade_date"])
    target_weights = target_weights.copy()
    target_weights["trade_date"] = pd.to_datetime(target_weights["trade_date"])
    if market.empty or market["trade_date"].isna().any() or target_weights["trade_date"].isna().any():
        raise ValueError("Market must be nonempty and dates must be valid")
    if market["symbol"].isna().any() or target_weights["symbol"].isna().any():
        raise ValueError("Symbols cannot be missing")
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
    last_mark_dates: dict[str, pd.Timestamp] = {}
    equity_rows = []
    trade_rows = []
    position_rows = []
    issue_rows = []
    generated_targets = []
    pending = None
    slippage = config.slippage_bps / 10_000.0

    for trade_date, day in market.groupby("trade_date", sort=True):
        if progress is not None and (not equity_rows or pd.Timestamp(equity_rows[-1]["trade_date"]).month != trade_date.month):
            progress(f"回测账本推进至 {trade_date.date()}")
        day = day.set_index("symbol", drop=False)
        previous_date = equity_rows[-1]["trade_date"] if equity_rows else None
        if "preclose" in day:
            for symbol, quantity in shares.items():
                if symbol in day.index and last_mark_dates.get(symbol) == previous_date:
                    reference = day.at[symbol, "preclose"]
                    if np.isfinite(reference) and reference > 0 and abs(last_marks[symbol] - reference) > .0051:
                        issue_rows.append({"trade_date": trade_date, "signal_date": pd.NaT,
                            "symbol": symbol, "side": "CORPORATE_ACTION",
                            "reason": "交易所昨收与前收不同：可能除权息；分红送转尚未逐笔入账，收益含近似误差",
                            "requested_shares": quantity, "unfilled_shares": None,
                            "previous_close": last_marks[symbol], "exchange_preclose": float(reference)})
        if pending is not None or (target_builder is None and trade_date in schedule):
            if target_builder is None:
                signal_date = schedule[trade_date]
                targets = target_weights[target_weights["trade_date"] == signal_date]
            else:
                signal_date, targets = pending
                pending = None
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
            for symbol in sorted(set(shares) | {s for s, w in target_map.items() if w > 0}):
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
                price = _execution_price(row, "SELL", slippage)
                gross = quantity * price
                fee = _commission(gross, config, trade_date)
                tax_rate = _stamp_duty_rate(trade_date, config)
                tax = gross * tax_rate
                if cash + gross < fee + tax - 1e-9:
                    record_issue(symbol, "SELL", "卖出收入与现金不足支付费用", quantity, quantity)
                    continue
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

            # Follow signal priority when fees/cash cannot finance every target.
            priority = targets.sort_values(["factor_rank", "symbol"]).symbol.tolist() if "factor_rank" in targets else sorted(target_map)
            for symbol in priority:
                weight = target_map[symbol]
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
                price = _execution_price(row, "BUY", slippage)
                quantity = affordable_quantity(quantity, price, cash, symbol, config, trade_date)
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
                fee = _commission(gross, config, trade_date)
                cash -= gross + fee
                shares[symbol] = shares.get(symbol, 0) + quantity
                # A missing later close cannot retrospectively prevent an open fill.
                last_marks.setdefault(symbol, raw_price)
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
                last_mark_dates[symbol] = trade_date
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
        if target_builder is not None:
            chosen = target_builder(trade_date, day.copy(), cash, dict(shares), dict(last_marks))
            if chosen is not None:
                require_columns(chosen, ["trade_date", "symbol", "target_weight"])
                w = pd.to_numeric(chosen["target_weight"], errors="coerce")
                if (not np.isfinite(w).all() or (w < 0).any() or w.sum() > 1 + 1e-9
                        or chosen.symbol.duplicated().any()
                        or not pd.to_datetime(chosen.trade_date).eq(trade_date).all()):
                    raise ValueError("Target builder returned invalid dated weights")
                pending = (trade_date, chosen.copy())
                if not chosen.empty:
                    generated_targets.append(chosen.copy())

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
                "previous_close",
                "exchange_preclose",
            ],
        ),
        targets=(pd.concat(generated_targets, ignore_index=True) if generated_targets
                 else target_weights.copy()),
    )
