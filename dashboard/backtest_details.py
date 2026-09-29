"""Shared experiment conditions and daily ledger views."""

import pandas as pd
import streamlit as st

from dashboard.components import number, pct
from quant_lab.dashboard.experiments import replay_day


def conditions(metadata: dict) -> None:
    st.subheader("本次回测条件")
    provenance = metadata.get("provenance", {})
    strategy = metadata.get("strategy", {})
    config = metadata.get("backtest", {})
    cols = st.columns(4)
    cols[0].metric("实际开始", metadata.get("actual_start", "未记录"))
    cols[1].metric("实际结束", metadata.get("actual_end", "未记录"))
    cols[2].metric("调仓频率", strategy.get("rebalance_frequency", "未记录"))
    cols[3].metric("初始资金", number(config.get("initial_cash"), 0))
    st.caption(
        f"采样：日频 · 成交：{provenance.get('execution_timing', '旧报告未记录')} · "
        f"数据截止：{provenance.get('data_end', '未记录')}"
    )
    st.caption(f"保存 / 生成时间：{metadata.get('created_at', '旧报告未记录')}")
    if config:
        st.caption(
            f"佣金 {pct(config.get('commission_rate'))} · 最低佣金 {number(config.get('minimum_commission'), 2)} 元 · "
            f"滑点 {number(config.get('slippage_bps'), 1)} bp · 过户费 {pct(config.get('transfer_fee_rate'), 3)}"
        )
        st.caption(
            f"卖出印花税：{config.get('stamp_duty_change_date', '未记录')} 起 "
            f"{pct(config.get('stamp_duty_rate'))}，此前 {pct(config.get('historical_stamp_duty_rate'))}"
        )
    st.write(f"股票池：{provenance.get('universe', '旧报告未保存股票池参数')}")
    st.write(f"因子：{', '.join(strategy.get('factor_weights', {})) or '旧报告未记录'}")
    with st.expander("查看完整参数与数据来源"):
        st.json(
            {"组合规则": strategy, "费用与资金": config, "数据与研究口径": provenance}
        )
    if not config:
        st.info("旧报告没有保存费用参数快照；下次生成正式研究报告后会补齐。")


def ledger(
    equity, targets, trades, positions, issues, *, key: str, issues_available=True
):
    st.subheader("逐日持仓与成交回放")
    dates = sorted(pd.to_datetime(equity.trade_date).dropna().unique())
    if not dates:
        st.info("没有可回放的交易日。")
        return
    date = st.selectbox(
        "回放交易日",
        dates,
        index=len(dates) - 1,
        format_func=lambda value: f"{pd.Timestamp(value):%Y-%m-%d}",
        key=key,
    )
    day = replay_day(equity, targets, trades, positions, issues, date)
    account = day["equity"].iloc[0]
    cols = st.columns(3)
    for column, name, label in zip(
        cols,
        ["cash", "market_value", "equity"],
        ["收盘现金", "收盘持仓市值", "收盘总资产"],
    ):
        column.metric(label, number(account.get(name), 2))
    signal = day["signal_date"]
    if signal is None:
        st.caption("当日没有可识别的调仓信号；目标表缺失时无法判断是否调仓。")
    else:
        st.caption(
            f"执行信号日期：{signal:%Y-%m-%d}；目标是该日收盘后的计划，持仓是回放日收盘的实际结果。"
        )
    for tab, table, empty in zip(
        st.tabs(["目标与信号", "实际成交", "收盘持仓", "未成交 / 部分成交"]),
        [day["targets"], day["trades"], day["positions"], day["issues"]],
        [
            "当日没有可展示的执行目标。",
            "当日没有成交。",
            "当日没有持仓记录。",
            "当日没有记录到执行受阻。"
            if issues_available
            else "旧报告未记录未成交原因，不能由成交表反推。",
        ],
    ):
        with tab:
            if table.empty:
                st.info(empty)
            else:
                st.dataframe(table, hide_index=True, width="stretch")
