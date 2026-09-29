"""Single-factor diagnostics and cross-factor comparison."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from dashboard.components import number, pct
from dashboard.context import page_intro, store
from quant_lab.dashboard import ArtifactError

FACTOR_NOTES = {
    "momentum_20_5": "20 日动量，跳过最近 5 日，观察较短趋势是否延续。",
    "momentum_60_5": "60 日动量，跳过最近 5 日，减少短期反转干扰。",
    "momentum_120_20": "较长期动量，跳过最近 20 日。",
    "reversal_5": "最近 5 日收益的反方向，检验短期反转。",
    "reversal_20": "最近 20 日收益的反方向。",
    "volatility_20": "最近 20 日收益波动率；配置方向为负时代表偏好低波动。",
    "volatility_60": "最近 60 日收益波动率。",
    "amihud_20": "价格变动相对成交额的冲击，数值较大通常表示流动性较弱。",
    "turnover_mean_20": "最近 20 日平均换手率。",
    "amount_momentum_20": "最近成交额相对过去水平的变化。",
    "price_volume_corr_20": "最近20日收益与成交额对数变化的相关性。",
    "downside_volatility_20": "近20日下跌收益平方的平均值再开方；负方向偏好下行波动较小的股票。",
    "overnight_reversal_5": "近5日隔夜开盘缺口的均值取负；假设隔夜涨跌可能反转，仍需验证。",
    "intraday_momentum_20": "用日线开收盘价计算近20日盘内收益均值；不需要分钟数据。",
}


def render() -> None:
    page_intro("因子研究", "选择一个因子，依次检查预测方向、稳定性、覆盖率和最新截面。")
    artifacts = store()
    from dashboard.research_runner import version_notice
    version_notice(artifacts)
    try:
        summary = artifacts.csv("factor_summary.csv")
        names = summary["factor"].astype(str).tolist()
    except ArtifactError as exc:
        st.error(str(exc))
        return

    if not names:
        st.info("尚无可用的因子诊断，请先生成日频研究报告。")
        return
    if st.session_state.get("selected_factor") not in names:
        st.session_state["selected_factor"] = names[0]
    factor = st.selectbox("选择因子", names, key="selected_factor")
    row = summary.loc[summary["factor"] == factor].iloc[0]
    if artifacts.exists(f"quantiles_{factor}.csv"):
        with st.expander("分层收益（未来收益标签，仅用于研究评价）"):
            quantiles = artifacts.csv(f"quantiles_{factor}.csv")
            label_cols = [c for c in quantiles if c.startswith("forward_return_")]
            if label_cols:
                st.dataframe(quantiles.groupby("quantile")[label_cols].mean().reset_index(), hide_index=True)
                if "label_coverage" in quantiles:
                    st.caption("未来标签覆盖率低时，组均值只代表有后续价格的子样本。分组使用信号日排名固定，未完成的未来收益保持缺失。")
                    st.dataframe(quantiles.groupby("quantile")[["assigned_count", "observed_count", "label_coverage"]].mean().reset_index(), hide_index=True)
                preference = "较低组" if row.get("configured_direction", 1) < 0 else "较高组"
                st.caption(f"组编号按原始因子处理分数从低到高排列，尚未乘配置方向；当前方向偏好{preference}。标签使用收盘到收盘收益，不是已扣费用的多空账户。")
    st.markdown(f"**直观含义：** {FACTOR_NOTES.get(factor, '请结合因子库源码确认定义。')}")

    cols = st.columns(6)
    cols[0].metric("原始 Mean IC", number(row.get("mean_ic"), 4))
    cols[1].metric("配置方向", "+1" if row.get("configured_direction", 1) > 0 else "-1")
    cols[2].metric("方向调整后 IC", number(row.get("oriented_mean_ic"), 4))
    cols[3].metric("原始 ICIR", number(row.get("icir"), 2))
    cols[4].metric("原始 IC > 0 比例", pct(row.get("win_rate")))
    cols[5].metric("有效日数", number(row.get("observations"), 0))
    if "next_open_mean_ic" in row:
        st.subheader("按可交易时点检验")
        checks = st.columns(3)
        checks[0].metric("次日开盘标签 IC", number(row.get("next_open_mean_ic"), 4))
        checks[1].metric("方向调整后 IC", number(row.get("next_open_oriented_ic"), 4))
        checks[2].metric("原始 IC 的 HAC t 值", number(row.get("hac_t"), 2))
        st.caption(f"信号在收盘后形成；标签从次日开盘持有 {int(row.get('forward_periods', 5))} 个交易日后开盘退出，尚未扣成本。原始IC均值95%近似区间 [{row.get('hac_ci_low', float('nan')):.4f}, {row.get('hac_ci_high', float('nan')):.4f}]；HAC处理序列相关，不校正多重试验，也不证明可交易盈利。方向为-1时，研究假设通常对应原始IC为负。")
        if artifacts.exists(f"annual_ic_{factor}.csv"):
            st.caption("逐年原始IC使用次日开盘标签；当年未结束或样本缺失时，days会少于完整交易年。")
            st.dataframe(artifacts.csv(f"annual_ic_{factor}.csv"), hide_index=True)

    try:
        if artifacts.exists(f"execution_ic_{factor}.csv"):
            timing = st.radio("IC曲线口径", ["次日开盘到后续开盘", "当日收盘到后续收盘"], horizontal=True)
            ic = artifacts.csv(f"execution_ic_{factor}.csv") if timing.startswith("次日") else artifacts.factor_ic(factor)
        else:
            ic = artifacts.factor_ic(factor)
        ic = ic.dropna(subset=["trade_date", "rank_ic"])
        ic["累计 IC"] = ic["rank_ic"].cumsum()
        left, right = st.columns(2)
        left.plotly_chart(
            px.line(ic, x="trade_date", y="rank_ic", title="每日 Rank IC"),
            width="stretch",
        )
        right.plotly_chart(
            px.line(ic, x="trade_date", y="累计 IC", title="累计 Rank IC（仅用于观察稳定性）"),
            width="stretch",
        )
    except ArtifactError as exc:
        st.info(str(exc))

    try:
        stability = artifacts.csv("factor_stability.csv")
        st.caption("以下历史窗口表使用方向调整后的分数和收盘标签，与上方原始IC、次日开盘逐年表分别核对。")
        selected = stability.loc[stability["factor"] == factor].copy()
        figure = px.bar(
            selected,
            x="window",
            y="mean_ic",
            color="mean_ic",
            color_continuous_scale="RdBu_r",
            color_continuous_midpoint=0,
            title="不同时间窗口的 Mean IC",
        )
        st.plotly_chart(figure, width="stretch")
    except ArtifactError as exc:
        st.info(str(exc))

    st.subheader("因子覆盖率")
    try:
        coverage = artifacts.csv("factor_coverage.csv")
        coverage = coverage.loc[coverage["factor"] == factor].dropna(subset=["coverage"])
        if coverage.empty:
            st.info("该因子没有可用的覆盖率记录。")
        else:
            latest_coverage = coverage.sort_values("trade_date").iloc[-1]
            st.caption(
                f"最新覆盖率 {pct(latest_coverage['coverage'])}；"
                f"有效区间平均覆盖率 {pct(coverage['coverage'].mean())}。"
            )
            st.plotly_chart(
                px.line(
                    coverage,
                    x="trade_date",
                    y="coverage",
                    title="每日股票覆盖率",
                ),
                width="stretch",
            )
    except ArtifactError as exc:
        st.info(str(exc))

    left, right = st.columns(2)
    with left:
        st.subheader("最新截面分布")
        try:
            snapshot = artifacts.factor_snapshot(factor)
            date = snapshot["trade_date"].max()
            st.caption(f"截面日期：{date:%Y-%m-%d}；只按需读取一个日期和一个因子。")
            st.plotly_chart(
                px.histogram(snapshot, x="factor_value", nbins=50, title=factor),
                width="stretch",
            )
        except (ArtifactError, ValueError) as exc:
            st.info(str(exc))
    with right:
        st.subheader("最新截面两端")
        try:
            display = snapshot[["symbol", "factor_value", "close"]]
            top = display.head(5).assign(位置="最高端")
            bottom = display.tail(5).sort_values("factor_value").assign(位置="最低端")
            st.dataframe(
                pd.concat([top, bottom], ignore_index=True),
                width="stretch",
                hide_index=True,
            )
        except (NameError, KeyError):
            st.caption("截面数据不可用。")

    st.subheader("因子相关性")
    try:
        correlation = artifacts.correlation()
        figure = go.Figure(
            go.Heatmap(
                z=correlation.values,
                x=correlation.columns,
                y=correlation.index,
                zmin=-1,
                zmax=1,
                colorscale="RdBu",
                reversescale=True,
                colorbar={"title": "相关系数"},
            )
        )
        figure.update_layout(height=650, margin={"l": 20, "r": 20, "t": 20, "b": 20})
        st.plotly_chart(figure, width="stretch")
    except ArtifactError as exc:
        st.info(str(exc))

    with st.expander("怎样读这一页？", expanded=False):
        st.markdown(
            """
            1. 先看**方向调整后的 IC**是否与研究假设一致。
            2. 再看年度窗口，判断效果是否只集中在少数年份。
            3. 检查覆盖率与有效日数，避免把大量缺失值误当成有效信号。
            4. 查看相关性，避免重复加入表达同一信息的因子。
            5. 最后才看最新排名；一次截面不能证明因子有效。
            """
        )
