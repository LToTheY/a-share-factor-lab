"""Config-driven ordinary-factor evaluation, composite and paper signals."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from quant_lab.backtest.engine import BacktestConfig, run_backtest
from quant_lab.backtest.metrics import performance_metrics, turnover_from_trades
from quant_lab.evaluation.diagnostics import (
    add_forward_returns,
    information_coefficient,
    quantile_returns,
    summarize_ic,
)
from quant_lab.evaluation.preprocess import preprocess_factor, zscore
from quant_lab.factors.library import compute_factor
from quant_lab.portfolio.weights import buffered_top_n_weights
from quant_lab.reporting import markdown_table, write_line_svg
from quant_lab.research.settings import ResearchSettings
from quant_lab.universe.filters import UniverseConfig, apply_universe


def _prepare_market(market: pd.DataFrame, settings: ResearchSettings) -> pd.DataFrame:
    result = apply_universe(
        market,
        UniverseConfig(
            min_listed_days=settings.min_listed_days,
            exclude_st=True,
            exclude_suspended=True,
            min_amount=settings.min_amount,
            require_known_status=settings.require_known_status,
            require_index_membership="in_index" in market,
        ),
    )
    if "adj_close" not in result:
        result["adj_close"] = result["close"] * result.get("adj_factor", 1.0)
    return result


def _factor_correlation(scores: pd.DataFrame, names: list[str]) -> pd.DataFrame:
    daily = []
    for _, group in scores.groupby("trade_date", sort=False):
        daily.append(group[names].corr(method="spearman"))
    if not daily:
        return pd.DataFrame(index=names, columns=names, dtype=float)
    return pd.concat(daily).groupby(level=0).mean().reindex(index=names, columns=names)


def run_factor_suite(
    market: pd.DataFrame,
    settings: ResearchSettings,
    output_dir: str | Path,
    next_trading_date: str | pd.Timestamp | None,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    prepared = _prepare_market(market, settings)
    labels = add_forward_returns(
        prepared, settings.forward_periods, price_col="adj_close"
    )[["trade_date", "symbol", f"forward_return_{settings.forward_periods}d"]]
    label_col = f"forward_return_{settings.forward_periods}d"
    keys = prepared[["trade_date", "symbol"]].copy()
    score_table = keys.copy()
    summaries = []
    score_names = []

    exposure_columns = ["trade_date", "symbol", "in_universe"]
    for column in ["market_cap", "industry"]:
        if column in prepared:
            exposure_columns.append(column)
    exposures = prepared[exposure_columns]
    for definition in settings.factors:
        factor = compute_factor(prepared, definition.name).merge(
            exposures,
            on=["trade_date", "symbol"],
            how="left",
            validate="one_to_one",
        )
        factor.loc[~factor["in_universe"], "factor"] = pd.NA
        factor = preprocess_factor(
            factor,
            n_mad=settings.winsor_n_mad,
            neutralize_size=settings.neutralize_size,
            neutralize_industry=settings.neutralize_industry,
        ).merge(labels, on=["trade_date", "symbol"], how="left", validate="one_to_one")
        score_name = definition.name
        score_names.append(score_name)
        score_table[score_name] = (
            factor["factor_processed"] * definition.direction
        ).to_numpy()
        ic = information_coefficient(
            factor,
            "factor_processed",
            label_col,
            min_observations=10,
        )
        ic_summary = summarize_ic(ic, periods_per_year=252 / settings.forward_periods)
        groups = quantile_returns(
            factor, "factor_processed", label_col, groups=settings.groups
        )
        group_means = groups.groupby("quantile")[label_col].mean()
        spread = (
            float(group_means.iloc[-1] - group_means.iloc[0])
            if len(group_means) >= 2
            else float("nan")
        )
        summaries.append(
            {
                "factor": definition.name,
                "configured_direction": definition.direction,
                **ic_summary,
                "oriented_mean_ic": ic_summary["mean_ic"] * definition.direction,
                "top_minus_bottom_5d": spread,
                "observations": int(ic.count()),
            }
        )
        ic.rename_axis("trade_date").reset_index().to_csv(
            output / f"ic_{definition.name}.csv", index=False
        )

    valid_count = score_table[score_names].notna().sum(axis=1)
    minimum_factors = max(1, len(score_names) // 2)
    score_table["composite_raw"] = score_table[score_names].mean(axis=1)
    score_table.loc[valid_count < minimum_factors, "composite_raw"] = pd.NA
    score_table["factor_processed"] = score_table.groupby("trade_date")[
        "composite_raw"
    ].transform(zscore)
    score_table = score_table.merge(labels, on=["trade_date", "symbol"], how="left")
    score_table = score_table.merge(
        prepared[["trade_date", "symbol", "close", "in_universe"]],
        on=["trade_date", "symbol"],
        how="left",
        validate="one_to_one",
    )
    score_table.loc[~score_table["in_universe"], "factor_processed"] = pd.NA
    composite_ic = information_coefficient(
        score_table, "factor_processed", label_col, min_observations=10
    )

    targets = buffered_top_n_weights(
        score_table,
        top_n=settings.top_n,
        exit_rank=settings.exit_rank,
        frequency=settings.rebalance_frequency,
        max_weight=settings.max_weight,
        next_trading_date=next_trading_date,
    )
    backtest_config = BacktestConfig(**settings.backtest)
    result = run_backtest(prepared, targets, backtest_config)
    portfolio = performance_metrics(result.equity)
    portfolio["turnover"] = turnover_from_trades(result.trades, result.equity)
    latest_date = pd.Timestamp(score_table["trade_date"].max())
    latest = score_table[
        (score_table["trade_date"] == latest_date) & score_table["in_universe"]
    ].copy()
    latest["factor_rank"] = latest["factor_processed"].rank(
        method="first", ascending=False
    )
    latest = latest.sort_values("factor_rank")

    factor_summary = pd.DataFrame(summaries)
    factor_corr = _factor_correlation(score_table, score_names)
    factor_summary.to_csv(
        output / "factor_summary.csv", index=False, encoding="utf-8-sig"
    )
    factor_corr.to_csv(output / "factor_correlation.csv", encoding="utf-8-sig")
    score_table.to_parquet(output / "factor_scores.parquet", index=False)
    latest.to_csv(output / "latest_signal.csv", index=False, encoding="utf-8-sig")
    targets.to_csv(output / "target_weights.csv", index=False, encoding="utf-8-sig")
    result.equity.to_csv(output / "equity.csv", index=False)
    result.trades.to_csv(output / "trades.csv", index=False)
    result.positions.to_csv(output / "positions.csv", index=False)
    write_line_svg(composite_ic, output / "composite_ic.svg", "Composite daily RankIC")
    write_line_svg(
        result.equity.set_index("trade_date")["equity"] / backtest_config.initial_cash,
        output / "equity.svg",
        "Composite strategy NAV",
    )
    summary = {
        "data_source": market.attrs.get("provider", "external"),
        "start_date": str(pd.Timestamp(market["trade_date"].min()).date()),
        "end_date": str(latest_date.date()),
        "next_trading_date": str(pd.Timestamp(next_trading_date).date())
        if next_trading_date is not None
        else None,
        "factor_count": len(score_names),
        "top_n": settings.top_n,
        "exit_rank": settings.exit_rank,
        "rebalance_frequency": settings.rebalance_frequency,
        "composite": summarize_ic(
            composite_ic, periods_per_year=252 / settings.forward_periods
        ),
        "portfolio": portfolio,
        "warning": market.attrs.get("research_warning", "Research use only."),
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=True),
        encoding="utf-8",
    )
    report = f"""# 普通多因子日频研究报告

> {summary["warning"]}

- 数据区间：{summary["start_date"]} 至 {summary["end_date"]}
- 因子数量：{summary["factor_count"]}
- 调仓频率：{summary["rebalance_frequency"]}
- 持仓数量：Top {summary["top_n"]}；退出缓冲：Rank {summary["exit_rank"]}
- 信号在收盘后形成，计划在下一交易日开盘人工复核后执行。

## 单因子评价

{markdown_table(factor_summary)}

## 综合因子

{markdown_table(pd.DataFrame([summary["composite"]]))}

## 组合回测

{markdown_table(pd.DataFrame([portfolio]))}

## 重要边界

- `latest_signal.csv`每天生成，但`next_day_orders.csv`可以是`NO_TRADE`。
- 原始价格用于成交、整手和费用；后复权价格用于因子及未来收益标签。
- 股份分红送转的现金流尚未逐笔进入账本，回测仍是研究近似，不是券商对账单。
- 操作清单只是纸面计划，开盘前仍需人工检查停牌、涨跌停、公告和实际资金。
- 首次指数快照之前的历史仍有当前成员回填偏差。
"""
    (output / "REPORT.md").write_text(report, encoding="utf-8")
    return summary, latest, targets
