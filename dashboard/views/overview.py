"""Research health and latest signal overview."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.components import number, pct
from dashboard.context import page_intro, store
from quant_lab.dashboard import ArtifactError


def render() -> None:
    page_intro("系统总览", "先确认数据是否新鲜、研究是否完整，再阅读任何收益指标。")
    artifacts = store()
    try:
        summary = artifacts.json("summary.json")
        status = artifacts.json("run_status.json")
    except ArtifactError as exc:
        st.error(str(exc))
        st.code(".\\scripts\\daily_update.ps1", language="powershell")
        return

    data = status.get("data", {})
    portfolio = summary.get("portfolio", {})
    benchmark = summary.get("benchmark", {})
    composite = summary.get("composite", {})

    first = st.columns(5)
    first[0].metric("数据截止日", data.get("complete_through", summary.get("end_date", "—")))
    first[1].metric("当前股票数", number(data.get("symbols"), 0))
    first[2].metric("因子数量", number(summary.get("factor_count"), 0))
    first[3].metric("数据审计", "通过" if status.get("audit_passed") else "未通过")
    first[4].metric("订单状态", status.get("order_status", "—"))

    st.subheader("研究结论概览")
    second = st.columns(5)
    second[0].metric("综合因子 IC", number(composite.get("mean_ic"), 4))
    second[1].metric("综合因子 ICIR", number(composite.get("icir"), 2))
    second[2].metric("策略年化收益", pct(portfolio.get("annual_return")))
    second[3].metric("基准年化收益", pct(benchmark.get("annual_return")))
    second[4].metric("年化超额收益", pct(summary.get("annual_excess_return_vs_benchmark")))

    if float(summary.get("annual_excess_return_vs_benchmark", 0.0)) < 0:
        st.warning(
            "当前完整区间策略年化收益低于诊断基准。这是重要研究结果："
            "后续应检查因子方向、成本、组合构造和样本外稳定性，而不是隐藏它。"
        )
    warning = summary.get("warning") or data.get("survivorship_warning")
    if warning:
        st.warning(f"数据口径提醒：{warning}")

    left, right = st.columns([1, 1])
    with left:
        st.subheader("关键产物检查")
        health = pd.DataFrame(artifacts.health())
        st.dataframe(health, width="stretch", hide_index=True)
    with right:
        st.subheader("最新运行信息")
        st.markdown(
            f"""
            - 研究区间：`{summary.get('research_start_date', '—')}` 至 `{summary.get('end_date', '—')}`
            - 下一交易日：`{summary.get('next_trading_date', '—')}`
            - 调仓频率：`{summary.get('rebalance_frequency', '—')}`
            - 组合规则：Top `{summary.get('top_n', '—')}`，跌出 Rank `{summary.get('exit_rank', '—')}` 后退出
            - 数据陈旧交易日数：`{data.get('stale_trading_days', '—')}`
            """
        )

    st.subheader("最新综合排名")
    try:
        latest = artifacts.csv("latest_signal.csv").sort_values("factor_rank").head(20)
        columns = [
            name
            for name in ("trade_date", "symbol", "factor_rank", "factor_processed", "close")
            if name in latest.columns
        ]
        st.dataframe(latest[columns], width="stretch", hide_index=True)
    except ArtifactError as exc:
        st.info(str(exc))
