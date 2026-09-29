"""Pure strategy-sandbox logic shared by the dashboard and tests."""

from __future__ import annotations

from dataclasses import dataclass, field
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
from quant_lab.portfolio.selection import AUDIT_COLUMNS, TARGET_COLUMNS, select_targets
from quant_lab.portfolio.weights import rebalance_dates


@dataclass(frozen=True)
class StrategySpec:
    """A long-only Top-N strategy assembled from preprocessed factor scores."""

    factor_weights: dict[str, float]
    top_n: int = 20
    exit_rank: int = 30
    rebalance_frequency: str = "W-FRI"
    max_weight: float = 0.05
    minimum_factor_coverage: float = 0.8
    selection_mode: str = "rank"
    cash_buffer: float = 0.0
    allow_star: bool = True
    allow_chinext: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.factor_weights, dict):
            raise TypeError("因子权重必须是名称到数值的字典")
        active = {
            name: float(weight)
            for name, weight in self.factor_weights.items()
            if float(weight) != 0.0
        }
        if not active:
            raise ValueError("至少需要启用一个非零权重因子")
        if not all(np.isfinite(list(active.values()))):
            raise ValueError("因子权重必须是有限数值")
        if (not isinstance(self.top_n, int) or not isinstance(self.exit_rank, int)
                or self.top_n <= 0 or self.exit_rank < self.top_n):
            raise ValueError("需要满足 exit_rank >= top_n > 0")
        if not 0 < self.max_weight <= 1:
            raise ValueError("单只股票权重上限必须在 0 到 1 之间")
        if not 0 < self.minimum_factor_coverage <= 1:
            raise ValueError("最低有效因子比例必须在 0 到 1 之间")
        if self.selection_mode not in {"rank", "affordable"}:
            raise ValueError("选股方式必须是 rank 或 affordable")
        if not 0 <= self.cash_buffer < 1:
            raise ValueError("现金预留必须在 0（含）到 1（不含）之间")
        if self.rebalance_frequency not in {"D", "W-FRI", "M"}:
            raise ValueError("调仓频率必须是 D、W-FRI 或 M")


@dataclass
class StrategyResult:
    scores: pd.DataFrame
    targets: pd.DataFrame
    backtest: BacktestResult
    benchmark: pd.DataFrame
    metrics: dict[str, float]
    benchmark_metrics: dict[str, float]
    diagnostics: dict[str, float | int]
    selection_audit: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=AUDIT_COLUMNS))


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
    if work.duplicated(["trade_date", "symbol"]).any():
        raise ValueError("因子分数包含重复股票日期")
    values = work[list(active)].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
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
    *,
    progress=None,
    next_trading_date=None,
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
    market = market.copy()
    market["trade_date"] = pd.to_datetime(market["trade_date"])
    selected_dates = set(rebalance_dates(signal.trade_date, spec.rebalance_frequency, next_trading_date))
    signal_days = {date: group for date, group in signal.groupby("trade_date") if date in selected_dates}
    cash_dates = set()
    if "force_cash" in scores:
        flags = scores.groupby("trade_date").force_cash
        if (flags.nunique() > 1).any():
            raise ValueError("同一交易日的空仓指令必须一致")
        cash_dates = set(flags.first().loc[lambda s: s.eq(True)].index)
    audits, decisions = [], []

    def choose(date, day, cash, holdings, marks):
        if date not in signal_days:
            return None
        cross_section = signal_days[date]
        if date in cash_dates:
            decisions.append(0)
            return pd.DataFrame(columns=TARGET_COLUMNS)
        if not cross_section.factor_processed.notna().any():
            # Missing research signals are not an instruction to liquidate.
            return None
        equity = cash + sum(q * marks[s] for s, q in holdings.items())
        targets, audit = select_targets(cross_section, day.reset_index(drop=True), holdings, equity, spec, config)
        audits.append(audit)
        decisions.append(len(targets))
        return targets

    result = run_backtest(market, pd.DataFrame(columns=TARGET_COLUMNS), config, target_builder=choose, progress=progress)
    targets = result.targets
    if not decisions:
        raise ValueError("当前区间没有有效调仓信号，请扩大日期区间或检查因子覆盖")
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
        "rebalance_count": len(decisions),
        "total_cost": costs,
        "average_holdings": float(
            result.positions.groupby("trade_date")["symbol"].nunique().reindex(result.equity.trade_date, fill_value=0).mean()
        ),
        "average_target_holdings": float(np.mean(decisions)),
        "average_cash_ratio": float((result.equity.cash / result.equity.equity).mean()),
        "cost_to_initial_cash": costs / config.initial_cash,
        "unfilled_events": int(result.execution_issues.side.isin(["BUY", "SELL", "REBALANCE"]).sum()),
        "stale_valuation_events": int(result.execution_issues.side.eq("VALUATION").sum()),
        "corporate_action_events": int(result.execution_issues.side.eq("CORPORATE_ACTION").sum()),
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
        selection_audit=pd.concat(audits, ignore_index=True) if audits else pd.DataFrame(columns=AUDIT_COLUMNS),
    )
