"""Complete MOM_60_5 study: audit, diagnostics, robustness and backtest."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from quant_lab.backtest.engine import BacktestConfig, run_backtest
from quant_lab.backtest.metrics import (
    equal_weight_benchmark,
    performance_metrics,
    turnover_from_trades,
)
from quant_lab.data.audit import audit_daily_data, write_audit_report
from quant_lab.data.panel import make_adjusted_market
from quant_lab.evaluation.diagnostics import (
    add_forward_returns,
    annual_ic_summary,
    factor_rank_autocorrelation,
    ic_decay,
    information_coefficient,
    quantile_returns,
    summarize_ic,
    top_group_turnover,
)
from quant_lab.evaluation.preprocess import preprocess_factor
from quant_lab.factors.library import momentum
from quant_lab.portfolio.weights import top_n_weights
from quant_lab.reporting import markdown_table, write_line_svg
from quant_lab.universe.filters import UniverseConfig, apply_universe


def _prepare_momentum(
    market: pd.DataFrame,
    lookback: int,
    skip: int,
    horizons: tuple[int, ...] = (1, 5, 10, 20),
    neutralize_size: bool | None = None,
    neutralize_industry: bool | None = None,
) -> pd.DataFrame:
    ordered = market.sort_values(["symbol", "trade_date"]).copy()
    base_columns = ["trade_date", "symbol", "close", "in_universe"]
    optional_columns = [
        column for column in ["market_cap", "industry"] if column in ordered
    ]
    result = ordered[base_columns + optional_columns].copy()
    result["factor"] = momentum(ordered, lookback, skip).to_numpy()
    result.loc[~result["in_universe"], "factor"] = np.nan
    if neutralize_size is None:
        neutralize_size = (
            "market_cap" in result and result["market_cap"].notna().mean() >= 0.99
        )
    if neutralize_industry is None:
        neutralize_industry = (
            "industry" in result and result["industry"].nunique(dropna=True) > 1
        )
    result = preprocess_factor(
        result,
        neutralize_size=neutralize_size,
        neutralize_industry=neutralize_industry,
    )
    for horizon in horizons:
        labeled = add_forward_returns(ordered, horizon, price_col="close")
        return_column = f"forward_return_{horizon}d"
        result = result.merge(
            labeled[["trade_date", "symbol", return_column]],
            on=["trade_date", "symbol"],
            how="left",
            validate="one_to_one",
        )
    return result.sort_values(["trade_date", "symbol"]).reset_index(drop=True)


def _robustness_grid(
    market: pd.DataFrame,
    config: BacktestConfig,
    top_n: int,
) -> pd.DataFrame:
    variants = [
        (20, 0, "W-FRI"),
        (60, 0, "W-FRI"),
        (60, 5, "W-FRI"),
        (120, 5, "W-FRI"),
        (120, 20, "W-FRI"),
        (60, 5, "M"),
    ]
    rows = []
    for lookback, skip, frequency in variants:
        research = _prepare_momentum(market, lookback, skip, horizons=(5,))
        ic = information_coefficient(
            research, "factor_processed", "forward_return_5d", min_observations=10
        )
        weights = top_n_weights(
            research,
            top_n=top_n,
            frequency=frequency,
            max_weight=max(0.03, 1.0 / max(1, top_n)),
        )
        result = run_backtest(market, weights, config)
        metrics = performance_metrics(result.equity)
        rows.append(
            {
                "lookback": lookback,
                "skip": skip,
                "frequency": frequency,
                "mean_rank_ic": ic.mean(),
                "ic_win_rate": (ic.dropna() > 0).mean(),
                "annual_return": metrics["annual_return"],
                "sharpe": metrics["sharpe"],
                "max_drawdown": metrics["max_drawdown"],
                "turnover": turnover_from_trades(result.trades, result.equity),
            }
        )
    return pd.DataFrame(rows)


def run_momentum_study(
    market: pd.DataFrame,
    output_dir: str | Path,
    strict_audit: bool = True,
    top_n: int = 20,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    audit = audit_daily_data(market)
    write_audit_report(audit, output / "audit")
    if strict_audit and audit.has_errors:
        raise ValueError(f"Data audit failed; inspect {output / 'audit' / 'audit.md'}")

    adjusted_market = make_adjusted_market(market)
    size_neutralized = bool(
        "market_cap" in adjusted_market
        and adjusted_market["market_cap"].notna().mean() >= 0.99
    )
    industry_neutralized = bool(
        "industry" in adjusted_market
        and adjusted_market["industry"].nunique(dropna=True) > 1
    )
    adjusted_market = apply_universe(
        adjusted_market,
        UniverseConfig(
            min_listed_days=120,
            exclude_st=True,
            exclude_suspended=True,
            min_amount=10_000_000,
            require_known_status=strict_audit,
            require_index_membership="in_index" in adjusted_market,
        ),
    )
    available = int(adjusted_market.groupby("trade_date")["in_universe"].sum().median())
    effective_top_n = max(1, min(top_n, available))
    research = _prepare_momentum(adjusted_market, 60, 5)
    primary_ic = information_coefficient(
        research, "factor_processed", "forward_return_5d", min_observations=10
    )
    annual_ic = annual_ic_summary(primary_ic)
    decay = ic_decay(
        research, "factor_processed", horizons=(1, 5, 10, 20), price_col="close"
    )
    groups = quantile_returns(
        research, "factor_processed", "forward_return_5d", groups=5
    )
    group_summary = groups.groupby("quantile", as_index=False)[
        "forward_return_5d"
    ].mean()
    rank_persistence = factor_rank_autocorrelation(research, "factor_processed")
    factor_turnover = top_group_turnover(research, "factor_processed")

    initial_cash = 1_000_000.0
    backtest_config = BacktestConfig(initial_cash=initial_cash)
    weights = top_n_weights(
        research,
        top_n=effective_top_n,
        frequency="W-FRI",
        max_weight=max(0.03, 1.0 / effective_top_n),
    )
    result = run_backtest(adjusted_market, weights, backtest_config)
    portfolio = performance_metrics(result.equity)
    portfolio["turnover"] = turnover_from_trades(result.trades, result.equity)
    portfolio["fees"] = (
        float(result.trades["fee"].sum()) if not result.trades.empty else 0.0
    )
    portfolio["tax"] = (
        float(result.trades["tax"].sum()) if not result.trades.empty else 0.0
    )
    benchmark = equal_weight_benchmark(
        adjusted_market,
        initial_cash,
        price_col="close",
    )
    benchmark_metrics = performance_metrics(benchmark)
    robustness = _robustness_grid(adjusted_market, backtest_config, effective_top_n)

    research.to_csv(output / "momentum_research.csv", index=False, encoding="utf-8-sig")
    primary_ic.rename_axis("trade_date").reset_index().to_csv(
        output / "rank_ic.csv", index=False
    )
    annual_ic.to_csv(output / "annual_ic.csv", index=False)
    decay.to_csv(output / "ic_decay.csv", index=False)
    groups.to_csv(output / "quantile_returns.csv", index=False)
    group_summary.to_csv(output / "quantile_summary.csv", index=False)
    rank_persistence.rename_axis("trade_date").reset_index().to_csv(
        output / "rank_persistence.csv", index=False
    )
    factor_turnover.rename_axis("trade_date").reset_index().to_csv(
        output / "factor_turnover.csv", index=False
    )
    weights.to_csv(output / "target_weights.csv", index=False)
    result.equity.to_csv(output / "equity.csv", index=False)
    result.trades.to_csv(output / "trades.csv", index=False)
    result.positions.to_csv(output / "positions.csv", index=False)
    benchmark.to_csv(output / "benchmark_equity.csv", index=False)
    robustness.to_csv(output / "robustness.csv", index=False)

    write_line_svg(primary_ic, output / "rank_ic.svg", "MOM_60_5 daily RankIC")
    strategy_curve = result.equity.set_index("trade_date")["equity"] / initial_cash
    write_line_svg(strategy_curve, output / "equity.svg", "MOM_60_5 strategy NAV")

    summary = {
        "data_source": (
            "synthetic"
            if market.attrs.get("synthetic")
            else market.attrs.get("provider", "external")
        ),
        "audit_passed": not audit.has_errors,
        "start_date": str(pd.Timestamp(market["trade_date"].min()).date()),
        "end_date": str(pd.Timestamp(market["trade_date"].max()).date()),
        "universe": market.attrs.get(
            "universe",
            "historical_index"
            if "in_index" in adjusted_market
            else "all_a_share_fallback",
        ),
        "universe_median_size": available,
        "effective_top_n": effective_top_n,
        "size_neutralized": size_neutralized,
        "industry_neutralized": industry_neutralized,
        "factor": summarize_ic(primary_ic),
        "factor_rank_persistence": float(rank_persistence.mean()),
        "factor_top_group_turnover": float(factor_turnover.mean()),
        "portfolio": portfolio,
        "benchmark": benchmark_metrics,
        "warning": (
            "Synthetic results are engineering evidence only."
            if market.attrs.get("synthetic")
            else market.attrs.get(
                "research_warning",
                "Verify data license and PIT fields before publication.",
            )
        ),
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=True),
        encoding="utf-8",
    )
    audit_path = (
        "data/processed/baostock_daily_audit/audit.md"
        if summary["data_source"] == "baostock"
        else "本报告目录下的audit/audit.md"
    )
    report = f"""# MOM_60_5因子研究报告

> {summary["warning"]}

## 研究设定

- 信号：`P(t-5) / P(t-60) - 1`
- 信号形成：t日收盘后；执行：下一交易日开盘
- 数据区间：{summary["start_date"]} 至 {summary["end_date"]}
- 股票池中位数：{available}；持仓数：{effective_top_n}
- 股票池口径：{summary["universe"]}
- 市值中性化：{size_neutralized}；行业中性化：{industry_neutralized}
- 数据审计通过：{summary["audit_passed"]}

## RankIC

![RankIC](rank_ic.svg)

{markdown_table(pd.DataFrame([summary["factor"]]))}

### 年度稳定性

{markdown_table(annual_ic)}

### 衰减

{markdown_table(decay)}

## 五分组平均未来5日收益

{markdown_table(group_summary)}

## 组合回测

![净值](equity.svg)

{markdown_table(pd.DataFrame([portfolio]))}

## 稳健性

{markdown_table(robustness)}

## 解释边界

- 回测使用连续复权价格近似总收益；生产账本仍需显式处理分红送转。
- 结果必须结合`{audit_path}`阅读；历史ST、停牌或涨跌停状态未知时不得对外发布。
- 五日未来收益的逐日IC存在标签重叠，ICIR年化仅作为统一比较口径。
- 正式简历只使用真实数据、固定配置和样本外结果。
"""
    (output / "REPORT.md").write_text(report, encoding="utf-8")
    return summary
