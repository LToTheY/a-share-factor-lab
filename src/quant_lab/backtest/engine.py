"""Transparent daily ledger with next-session execution and A-share constraints."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from quant_lab.data.schema import require_columns


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: float = 1_000_000.0
    commission_rate: float = 0.0003
    stamp_duty_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    slippage_bps: float = 5.0
    minimum_commission: float = 5.0
    lot_size: int = 100


@dataclass
class BacktestResult:
    equity: pd.DataFrame
    trades: pd.DataFrame
    positions: pd.DataFrame


def _commission(gross: float, config: BacktestConfig) -> float:
    commission = max(config.minimum_commission, gross * config.commission_rate)
    return commission + gross * config.transfer_fee_rate


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
    trading_dates = pd.DatetimeIndex(sorted(market["trade_date"].unique()))
    schedule = _execution_schedule(target_weights["trade_date"], trading_dates)

    cash = float(config.initial_cash)
    shares: dict[str, int] = {}
    equity_rows = []
    trade_rows = []
    position_rows = []
    slippage = config.slippage_bps / 10_000.0

    for trade_date, day in market.groupby("trade_date", sort=True):
        day = day.set_index("symbol", drop=False)
        if trade_date in schedule:
            signal_date = schedule[trade_date]
            targets = target_weights[target_weights["trade_date"] == signal_date]
            target_map = dict(zip(targets["symbol"], targets["target_weight"]))
            open_equity = cash + sum(
                quantity * float(day.at[symbol, "open"])
                for symbol, quantity in shares.items()
                if symbol in day.index
            )

            # Sell first so proceeds can finance purchases.
            for symbol in sorted(set(shares) | set(target_map)):
                if symbol not in day.index:
                    continue
                row = day.loc[symbol]
                desired_value = open_equity * target_map.get(symbol, 0.0)
                raw_price = float(row["open"])
                desired_shares = int(
                    np.floor(desired_value / raw_price / config.lot_size)
                    * config.lot_size
                )
                current_shares = shares.get(symbol, 0)
                quantity = current_shares - desired_shares
                blocked = bool(row.get("is_suspended", False)) or bool(
                    row.get("is_limit_down", False)
                )
                if quantity <= 0 or blocked:
                    continue
                price = raw_price * (1 - slippage)
                gross = quantity * price
                fee = _commission(gross, config)
                tax = gross * config.stamp_duty_rate
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
                    }
                )

            for symbol, weight in sorted(target_map.items()):
                if symbol not in day.index:
                    continue
                row = day.loc[symbol]
                blocked = bool(row.get("is_suspended", False)) or bool(
                    row.get("is_limit_up", False)
                )
                if blocked:
                    continue
                raw_price = float(row["open"])
                desired_value = open_equity * weight
                desired_shares = int(
                    np.floor(desired_value / raw_price / config.lot_size)
                    * config.lot_size
                )
                quantity = desired_shares - shares.get(symbol, 0)
                if quantity <= 0:
                    continue
                price = raw_price * (1 + slippage)
                # Reduce by lots until cash covers price and commission.
                while quantity > 0:
                    gross = quantity * price
                    fee = _commission(gross, config)
                    if gross + fee <= cash:
                        break
                    quantity -= config.lot_size
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
                    }
                )

            shares = {
                symbol: quantity for symbol, quantity in shares.items() if quantity
            }

        market_value = 0.0
        for symbol, quantity in shares.items():
            if symbol not in day.index:
                continue
            value = quantity * float(day.at[symbol, "close"])
            market_value += value
            position_rows.append(
                {
                    "trade_date": trade_date,
                    "symbol": symbol,
                    "shares": quantity,
                    "close": float(day.at[symbol, "close"]),
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
    ]
    position_columns = ["trade_date", "symbol", "shares", "close", "market_value"]
    return BacktestResult(
        equity=pd.DataFrame(equity_rows),
        trades=pd.DataFrame(trade_rows, columns=trade_columns),
        positions=pd.DataFrame(position_rows, columns=position_columns),
    )
