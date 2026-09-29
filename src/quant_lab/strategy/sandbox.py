"""Pure strategy-sandbox logic shared by the dashboard and tests."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

import numpy as np
import pandas as pd

from quant_lab.backtest.engine import BacktestConfig, BacktestResult, run_backtest
from quant_lab.backtest.metrics import (
    equal_weight_benchmark,
    performance_metrics,
    turnover_from_trades,
)
from quant_lab.data.schema import require_columns
from quant_lab.evaluation.preprocess import zscore
from quant_lab.portfolio.weights import buffered_top_n_weights


@dataclass(frozen=True)
class StrategySpec:
    """A long-only Top-N strategy assembled from preprocessed factor scores."""

    factor_weights: dict[str, float]
    top_n: int = 20
    exit_rank: int = 30
    rebalance_frequency: str = "W-FRI"
    max_weight: float = 0.05
    minimum_factor_coverage: float = 0.8

    def __post_init__(self) -> None:
        active = {
            name: float(weight)
            for name, weight in self.factor_weights.items()
            if float(weight) != 0.0
        }
        if not active:
            raise ValueError("至少需要启用一个非零权重因子")
        if not all(np.isfinite(list(active.values()))):
            raise ValueError("因子权重必须是有限数值")
        if self.top_n <= 0 or self.exit_rank < self.top_n:
            raise ValueError("需要满足 exit_rank >= top_n > 0")
        if not 0 < self.max_weight <= 1:
            raise ValueError("单只股票权重上限必须在 0 到 1 之间")
        if not 0 < self.minimum_factor_coverage <= 1:
            raise ValueError("最低有效因子比例必须在 0 到 1 之间")


@dataclass
class StrategyResult:
    scores: pd.DataFrame
    targets: pd.DataFrame
    backtest: BacktestResult
    benchmark: pd.DataFrame
    metrics: dict[str, float]
    benchmark_metrics: dict[str, float]
    diagnostics: dict[str, float | int]


def build_composite_signal(scores: pd.DataFrame, spec: StrategySpec) -> pd.DataFrame:
    """Combine oriented scores while handling partial missingness per row."""
    active = {
        name: float(weight)
        for name, weight in spec.factor_weights.items()
        if float(weight) != 0.0
    }
    require_columns(scores, ["trade_date", "symbol", "in_universe", *active])
    work = scores[["trade_date", "symbol", "in_universe", *active]].copy()
    work["trade_date"] = pd.to_datetime(work["trade_date"])
    values = work[list(active)].apply(pd.to_numeric, errors="coerce")
    signed_weights = pd.Series(active, dtype=float)
    absolute_weights = signed_weights.abs()
    valid = values.notna()
    denominator = valid.mul(absolute_weights, axis=1).sum(axis=1)
    numerator = values.fillna(0.0).mul(signed_weights, axis=1).sum(axis=1)
    minimum_valid = max(1, ceil(len(active) * spec.minimum_factor_coverage))
    valid_count = valid.sum(axis=1)
    work["valid_factor_count"] = valid_count
    work["composite_raw"] = numerator / denominator.replace(0.0, np.nan)
    work.loc[valid_count < minimum_valid, "composite_raw"] = np.nan
    work.loc[~work["in_universe"].fillna(False).astype(bool), "composite_raw"] = np.nan
    work["factor_processed"] = work.groupby("trade_date")[
        "composite_raw"
    ].transform(zscore)
    eligible = work["in_universe"].fillna(False).astype(bool)
    work.loc[~eligible, "factor_processed"] = np.nan
    return work


def annual_returns(equity: pd.DataFrame) -> pd.DataFrame:
    """Compound daily changes within each calendar year."""
    require_columns(equity, ["trade_date", "equity"])
    work = equity.sort_values("trade_date").copy()
    work["trade_date"] = pd.to_datetime(work["trade_date"])
    work["return"] = work["equity"].astype(float).pct_change().fillna(0.0)
    annual = work.groupby(work["trade_date"].dt.year)["return"].apply(
        lambda values: float((1.0 + values).prod() - 1.0)
    )
    return annual.rename_axis("year").rename("return").reset_index()


def run_strategy(
    scores: pd.DataFrame,
    market: pd.DataFrame,
    spec: StrategySpec,
    backtest_config: BacktestConfig | None = None,
) -> StrategyResult:
    """Build targets and run a cost-aware backtest without writing any state."""
    config = backtest_config or BacktestConfig()
    require_columns(
        market,
        [
            "trade_date",
            "symbol",
            "open",
            "close",
            "adj_close",
            "in_index",
        ],
    )
    signal = build_composite_signal(scores, spec)
    targets = buffered_top_n_weights(
        signal,
        signal_col="factor_processed",
        top_n=spec.top_n,
        exit_rank=spec.exit_rank,
        frequency=spec.rebalance_frequency,
        max_weight=spec.max_weight,
    )
    if targets.empty:
        raise ValueError("当前参数没有生成任何目标持仓，请扩大日期区间或降低筛选要求")
    market = market.copy()
    market["trade_date"] = pd.to_datetime(market["trade_date"])
    result = run_backtest(market, targets, config)
    portfolio_metrics = performance_metrics(result.equity)
    portfolio_metrics["turnover"] = turnover_from_trades(
        result.trades, result.equity
    )
    benchmark = equal_weight_benchmark(
        market,
        config.initial_cash,
        eligibility_col="in_index",
        price_col="adj_close",
    )
    benchmark_metrics = performance_metrics(benchmark)
    costs = (
        float(result.trades[["fee", "tax"]].sum().sum())
        if not result.trades.empty
        else 0.0
    )
    diagnostics: dict[str, float | int] = {
        "trade_count": len(result.trades),
        "rebalance_count": int(targets["trade_date"].nunique()),
        "total_cost": costs,
        "average_holdings": float(
            result.positions.groupby("trade_date")["symbol"].nunique().reindex(result.equity.trade_date, fill_value=0).mean()
        ),
        "average_target_holdings": float(targets.groupby("trade_date").symbol.nunique().mean()),
        "average_cash_ratio": float((result.equity.cash / result.equity.equity).mean()),
        "cost_to_initial_cash": costs / config.initial_cash,
        "unfilled_events": len(result.execution_issues),
        "annual_excess_return": float(
            portfolio_metrics["annual_return"] - benchmark_metrics["annual_return"]
        ),
    }
    return StrategyResult(
        scores=signal,
        targets=targets,
        backtest=result,
        benchmark=benchmark,
        metrics=portfolio_metrics,
        benchmark_metrics=benchmark_metrics,
        diagnostics=diagnostics,
    )
