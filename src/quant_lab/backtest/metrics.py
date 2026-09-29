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
    groups = ["symbol", "research_segment"] if "research_segment" in work else ["symbol"]
    work["benchmark_return"] = work.groupby(groups, sort=False)[
        price_col
    ].pct_change(fill_method=None)
    # A close-to-close return is earned by the basket held at the previous
    # close. Today's newly eligible stock cannot contribute an already elapsed
    # overnight/day return. Also reject multi-session gaps in generic panels.
    observed_dates = pd.DatetimeIndex(sorted(pd.to_datetime(work.trade_date).unique()))
    preceding = pd.Series(observed_dates[:-1], index=observed_dates[1:])
    prior_observed = work.groupby("symbol", sort=False).trade_date.shift()
    adjacent = prior_observed.eq(work.trade_date.map(preceding))
    eligible = work.groupby(groups, sort=False)[eligibility_col].shift().eq(True) & adjacent
    daily = work.loc[eligible].groupby("trade_date")["benchmark_return"].mean()
    all_dates = observed_dates
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
    if not np.isfinite(values).all() or (values < 0).any() or values.iloc[0] <= 0:
        raise ValueError("Equity must be finite and nonnegative, with positive initial equity")
    if isinstance(periods_per_year, bool) or not isinstance(periods_per_year, int) or periods_per_year <= 0:
        raise ValueError("periods_per_year must be a positive integer")
    if "trade_date" in equity:
        dates = pd.to_datetime(equity.trade_date)
        if dates.isna().any() or dates.duplicated().any() or not dates.is_monotonic_increasing:
            raise ValueError("Equity dates must be unique, valid and increasing")
    previous = values.shift()
    if (previous.eq(0) & values.gt(0)).any():
        raise ValueError("Equity cannot recover from zero without a cash-flow model")
    returns = values.pct_change(fill_method=None).mask(previous.eq(0) & values.eq(0), 0.).iloc[1:]
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
