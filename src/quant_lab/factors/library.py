"""A compact factor library with strict per-symbol rolling operations."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from quant_lab.data.schema import require_columns

FactorFunction = Callable[[pd.DataFrame], pd.Series]


def _adjusted_close(frame: pd.DataFrame) -> pd.Series:
    if "adj_close" in frame:
        return pd.to_numeric(frame["adj_close"], errors="coerce")
    adjustment = frame.get("adj_factor", 1.0)
    return frame["close"] * adjustment


def momentum_20_5(frame: pd.DataFrame) -> pd.Series:
    return momentum(frame, lookback=20, skip=5)


def momentum_60_5(frame: pd.DataFrame) -> pd.Series:
    """Medium-term momentum: price(t-5) / price(t-60) - 1."""
    return momentum(frame, lookback=60, skip=5)


def momentum_120_20(frame: pd.DataFrame) -> pd.Series:
    return momentum(frame, lookback=120, skip=20)


def momentum(frame: pd.DataFrame, lookback: int = 60, skip: int = 5) -> pd.Series:
    """Configurable adjusted-price momentum for robustness experiments."""
    if lookback <= skip or skip < 0:
        raise ValueError("Require lookback > skip >= 0")
    require_columns(frame, ["symbol", "close"])
    price = _adjusted_close(frame)
    grouped = price.groupby(frame["symbol"], sort=False)
    return grouped.shift(skip) / grouped.shift(lookback) - 1.0


def reversal_5(frame: pd.DataFrame) -> pd.Series:
    """Negative five-day adjusted return."""
    require_columns(frame, ["symbol", "close"])
    price = _adjusted_close(frame)
    return -price.groupby(frame["symbol"], sort=False).pct_change(5)


def reversal_20(frame: pd.DataFrame) -> pd.Series:
    """Negative twenty-day adjusted return."""
    require_columns(frame, ["symbol", "close"])
    price = _adjusted_close(frame)
    return -price.groupby(frame["symbol"], sort=False).pct_change(20)


def amihud_20(frame: pd.DataFrame) -> pd.Series:
    """20-day average absolute return divided by CNY turnover."""
    require_columns(frame, ["symbol", "close", "amount"])
    price = _adjusted_close(frame)
    returns = price.groupby(frame["symbol"], sort=False).pct_change()
    daily_illiquidity = returns.abs() / frame["amount"].replace(0, np.nan)
    return (
        daily_illiquidity.groupby(frame["symbol"], sort=False)
        .rolling(20, min_periods=15)
        .mean()
        .reset_index(level=0, drop=True)
    )


def volatility_20(frame: pd.DataFrame) -> pd.Series:
    """20-day realized volatility of daily adjusted returns."""
    price = _adjusted_close(frame)
    returns = price.groupby(frame["symbol"], sort=False).pct_change()
    return (
        returns.groupby(frame["symbol"], sort=False)
        .rolling(20, min_periods=15)
        .std()
        .reset_index(level=0, drop=True)
    )


def volatility_60(frame: pd.DataFrame) -> pd.Series:
    """60-day realized volatility of daily adjusted returns."""
    price = _adjusted_close(frame)
    returns = price.groupby(frame["symbol"], sort=False).pct_change()
    return (
        returns.groupby(frame["symbol"], sort=False)
        .rolling(60, min_periods=40)
        .std()
        .reset_index(level=0, drop=True)
    )


def turnover_mean_20(frame: pd.DataFrame) -> pd.Series:
    """Twenty-day mean turnover rate."""
    require_columns(frame, ["symbol", "turnover_rate"])
    return (
        frame["turnover_rate"]
        .groupby(frame["symbol"], sort=False)
        .rolling(20, min_periods=15)
        .mean()
        .reset_index(level=0, drop=True)
    )


def amount_momentum_20(frame: pd.DataFrame) -> pd.Series:
    """Recent five-day amount relative to its twenty-day mean."""
    require_columns(frame, ["symbol", "amount"])
    grouped = frame["amount"].replace(0, np.nan).groupby(frame["symbol"], sort=False)
    recent = grouped.rolling(5, min_periods=4).mean().reset_index(level=0, drop=True)
    baseline = (
        grouped.rolling(20, min_periods=15).mean().reset_index(level=0, drop=True)
    )
    return recent / baseline - 1.0


def price_volume_corr_20(frame: pd.DataFrame) -> pd.Series:
    """Rolling correlation between adjusted returns and amount changes."""
    require_columns(frame, ["symbol", "amount", "close"])
    price = _adjusted_close(frame)
    returns = price.groupby(frame["symbol"], sort=False).pct_change()
    amount_change = (
        np.log(frame["amount"].replace(0, np.nan))
        .groupby(frame["symbol"], sort=False)
        .diff()
    )
    output = pd.Series(np.nan, index=frame.index, dtype=float)
    for positions in frame.groupby("symbol", sort=False).groups.values():
        idx = pd.Index(positions)
        output.loc[idx] = (
            returns.loc[idx]
            .rolling(20, min_periods=15)
            .corr(amount_change.loc[idx])
            .to_numpy()
        )
    return output


def idiosyncratic_volatility_60(frame: pd.DataFrame) -> pd.Series:
    """Rolling CAPM residual volatility using equal-weight market return."""
    require_columns(frame, ["trade_date", "symbol", "close"])
    price = _adjusted_close(frame)
    returns = price.groupby(frame["symbol"], sort=False).pct_change()
    market = returns.groupby(frame["trade_date"]).transform("mean")
    output = pd.Series(np.nan, index=frame.index, dtype=float)

    for positions in frame.groupby("symbol", sort=False).groups.values():
        idx = pd.Index(positions)
        stock_return = returns.loc[idx]
        market_return = market.loc[idx]
        covariance = stock_return.rolling(60, min_periods=40).cov(market_return)
        variance = market_return.rolling(60, min_periods=40).var()
        beta = covariance / variance.replace(0, np.nan)
        residual = stock_return - beta * market_return
        output.loc[idx] = residual.rolling(60, min_periods=40).std().to_numpy()
    return output


def book_to_price(frame: pd.DataFrame) -> pd.Series:
    """Point-in-time book value divided by market capitalization."""
    require_columns(frame, ["book_value", "market_cap"])
    return frame["book_value"] / frame["market_cap"].replace(0, np.nan)


def earnings_yield(frame: pd.DataFrame) -> pd.Series:
    """Point-in-time trailing earnings divided by market capitalization."""
    require_columns(frame, ["net_profit_ttm", "market_cap"])
    return frame["net_profit_ttm"] / frame["market_cap"].replace(0, np.nan)


FACTOR_REGISTRY: dict[str, FactorFunction] = {
    "momentum_20_5": momentum_20_5,
    "momentum_60_5": momentum_60_5,
    "momentum_120_20": momentum_120_20,
    "reversal_5": reversal_5,
    "reversal_20": reversal_20,
    "amihud_20": amihud_20,
    "volatility_20": volatility_20,
    "volatility_60": volatility_60,
    "turnover_mean_20": turnover_mean_20,
    "amount_momentum_20": amount_momentum_20,
    "price_volume_corr_20": price_volume_corr_20,
    "ivol_60": idiosyncratic_volatility_60,
    "book_to_price": book_to_price,
    "earnings_yield": earnings_yield,
}


def compute_factor(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    """Compute one named factor and return a canonical signal table."""
    if name not in FACTOR_REGISTRY:
        raise KeyError(
            f"Unknown factor {name!r}; choose from {sorted(FACTOR_REGISTRY)}"
        )
    ordered = frame.sort_values(["symbol", "trade_date"]).copy()
    values = FACTOR_REGISTRY[name](ordered)
    result = ordered[["trade_date", "symbol"]].copy()
    result["factor"] = values.to_numpy()
    if "in_universe" in ordered:
        result.loc[~ordered["in_universe"].to_numpy(), "factor"] = np.nan
    return result.sort_values(["trade_date", "symbol"]).reset_index(drop=True)
