"""Portfolio performance metrics with explicit annualization assumptions."""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant_lab.data.schema import require_columns


def equal_weight_benchmark(
    market: pd.DataFrame,
    initial_cash: float,
    *,
    eligibility_col: str = "in_universe",
    price_col: str = "adj_close",
    start_date: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Build a diagnostic equal-weight benchmark without crossing membership gaps.

    Returns are calculated on each symbol's complete price history before the
    point-in-time eligibility mask is applied. This prevents a stock that leaves
    and later re-enters the universe from contributing a multi-day return as if it
    occurred in one session. The result is a frictionless daily-rebalanced
    diagnostic, not a replication of an official index.
    """
    require_columns(market, ["trade_date", "symbol", eligibility_col, price_col])
    if initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    work = market.sort_values(["symbol", "trade_date"]).copy()
    work["benchmark_return"] = work.groupby("symbol", sort=False)[
        price_col
    ].pct_change(fill_method=None)
    eligible = work[eligibility_col].fillna(False).astype(bool)
    daily = work.loc[eligible].groupby("trade_date")["benchmark_return"].mean()
    all_dates = pd.DatetimeIndex(sorted(pd.to_datetime(market["trade_date"]).unique()))
    daily = daily.reindex(all_dates).fillna(0.0)
    if start_date is not None:
        daily = daily[daily.index >= pd.Timestamp(start_date)].copy()
        if not daily.empty:
            daily.iloc[0] = 0.0
    if daily.empty:
        raise ValueError("Benchmark has no observations in the requested period")
    return pd.DataFrame(
        {
            "trade_date": daily.index,
            "return": daily.to_numpy(),
            "equity": initial_cash * (1.0 + daily).cumprod().to_numpy(),
        }
    )


def performance_metrics(
    equity: pd.DataFrame,
    periods_per_year: int = 252,
) -> dict[str, float]:
    if equity.empty or "equity" not in equity:
        raise ValueError("Equity table is empty or missing 'equity'")
    values = equity["equity"].astype(float)
    returns = values.pct_change().dropna()
    if returns.empty:
        raise ValueError("At least two equity observations are required")
    years = len(returns) / periods_per_year
    total_return = values.iloc[-1] / values.iloc[0] - 1.0
    annual_return = (1.0 + total_return) ** (1.0 / years) - 1.0 if years else np.nan
    annual_volatility = returns.std(ddof=1) * np.sqrt(periods_per_year)
    sharpe = (
        returns.mean() / returns.std(ddof=1) * np.sqrt(periods_per_year)
        if returns.std(ddof=1) > 0
        else np.nan
    )
    drawdown = values / values.cummax() - 1.0
    max_drawdown = float(drawdown.min())
    calmar = annual_return / abs(max_drawdown) if max_drawdown < 0 else np.nan
    return {
        "total_return": float(total_return),
        "annual_return": float(annual_return),
        "annual_volatility": float(annual_volatility),
        "sharpe": float(sharpe),
        "max_drawdown": max_drawdown,
        "calmar": float(calmar),
    }


def turnover_from_trades(trades: pd.DataFrame, equity: pd.DataFrame) -> float:
    if trades.empty:
        return 0.0
    average_equity = equity["equity"].mean()
    if average_equity <= 0:
        return np.nan
    return float(trades["gross"].sum() / 2.0 / average_equity)
