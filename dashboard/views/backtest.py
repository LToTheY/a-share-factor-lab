"""Portfolio backtest visualisation."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.backtest_details import conditions, ledger
from dashboard.components import drawdown_figure, equity_figure, number, pct
from dashboard.context import page_intro, store
from quant_lab.dashboard import ArtifactError


def render() -> None:
    page_intro(
        "回测分析", "同时查看收益、回撤、换手和交易成本，不用单一收益率判断策略。"
    )
    artifacts = store()
    try:
        summary = artifacts.json("summary.json")
        strategy = artifacts.csv("equity.csv")
        benchmark_frame = artifacts.csv("benchmark_equity.csv")
    except ArtifactError as exc:
        st.error(str(exc))
        return

    portfolio = summary.get("portfolio", {})
    benchmark = summary.get("benchmark", {})
    settings = summary.get("research_settings", {})
    conditions(
        {
            "actual_start": str(strategy.trade_date.min().date()),
            "actual_end": str(strategy.trade_date.max().date()),
            "created_at": summary.get("generated_at", "旧报告未记录"),
            "strategy": {
                "factor_weights": {
                    item["name"]: item["direction"]
                    for item in settings.get("factors", [])
                },
                "rebalance_frequency": summary.get("rebalance_frequency", "未记录"),
                "top_n": summary.get("top_n"),
                "exit_rank": summary.get("exit_rank"),
                "max_weight": settings.get("max_weight", "未记录"),
            },
            "backtest": summary.get("backtest_config", {}),
            "provenance": {
                "data_end": summary.get("end_date", "未记录"),
                "execution_timing": summary.get("execution_timing", "旧报告未记录"),
                "universe": "报告记录的股票池筛选参数见下方"
                if settings
                else "旧报告未保存股票池参数",
                "research_settings": settings,
                "data_source": summary.get("data_source", "未记录"),
                "benchmark": benchmark.get("definition", "未记录"),
            },
        }
    )
    cols = st.columns(6)
    cols[0].metric("策略总收益", pct(portfolio.get("total_return")))
    cols[1].metric("策略年化", pct(portfolio.get("annual_return")))
    cols[2].metric("基准年化", pct(benchmark.get("annual_return")))
    cols[3].metric("Sharpe", number(portfolio.get("sharpe"), 2))
    cols[4].metric("最大回撤", pct(portfolio.get("max_drawdown")))
    cols[5].metric("累计换手", number(portfolio.get("turnover"), 1))

    st.plotly_chart(
        equity_figure(strategy, benchmark_frame, "策略与诊断基准"),
        width="stretch",
    )
    st.plotly_chart(drawdown_figure(strategy), width="stretch")

    if portfolio.get("annual_return", 0) < benchmark.get("annual_return", 0):
        st.warning(
            "当前策略没有跑赢基准。回测页面的作用是发现问题，"
            "不能据此推断未来收益，也不应选择性删除失败区间。"
        )

    left, right = st.columns(2)
    with left:
        st.subheader("最近交易")
        try:
            trades = artifacts.csv("trades.csv").sort_values(
                "trade_date", ascending=False
            )
            useful = [
                name
                for name in (
                    "trade_date",
                    "signal_date",
                    "symbol",
                    "side",
                    "shares",
                    "price",
                    "fee",
                    "tax",
                )
                if name in trades.columns
            ]
            st.dataframe(trades[useful].head(100), width="stretch", hide_index=True)
        except ArtifactError as exc:
            st.info(str(exc))
    with right:
        st.subheader("最近持仓快照")
        try:
            positions = artifacts.csv("positions.csv")
            latest_date = positions["trade_date"].max()
            latest = positions.loc[positions["trade_date"] == latest_date]
            st.caption(f"持仓日期：{latest_date:%Y-%m-%d}")
            st.dataframe(
                latest.sort_values("market_value", ascending=False),
                width="stretch",
                hide_index=True,
            )
        except (ArtifactError, ValueError) as exc:
            st.info(str(exc))

    replay_tables = {}
    for name in ("target_weights", "trades", "positions", "execution_issues"):
        try:
            replay_tables[name] = artifacts.csv(f"{name}.csv")
        except ArtifactError as exc:
            replay_tables[name] = pd.DataFrame()
            if name != "execution_issues":
                st.info(str(exc))
    ledger(
        strategy,
        replay_tables["target_weights"],
        replay_tables["trades"],
        replay_tables["positions"],
        replay_tables["execution_issues"],
        key="formal_replay_date",
        issues_available=artifacts.exists("execution_issues.csv"),
    )

    with st.expander("回测边界", expanded=True):
        st.markdown(
            f"""
            - 基准定义：{benchmark.get("definition", "见研究报告")}
            - 回测是历史模拟，不是收益承诺。
            - 需要继续检查未来函数、幸存者偏差、成本假设、涨跌停和停牌约束。
            - 当前页面只读取已有回测文件，不允许从浏览器修改模拟账户或发送订单。
            """
        )
