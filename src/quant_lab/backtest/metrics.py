"""Portfolio performance metrics with explicit annualization assumptions."""

from __future__ import annotations

import numpy as np
import pandas as pd


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
