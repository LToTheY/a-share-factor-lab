"""Latest-data refresh and manual paper-rebalance review."""

from __future__ import annotations

import hashlib
import json

import pandas as pd
import streamlit as st

from dashboard.context import PROJECT_ROOT, page_intro
from dashboard.order_display import order_preview
from quant_lab.data.freshness import current_result_status


def render() -> None:
    page_intro("今日调仓检查", "先更新日行情、核验完整性，再查看最新排名和纸面调仓建议。")
    st.caption("按北京时间判断交易日。默认18:00以后要求当天完整日行情；此前使用上一交易日。")
    st.info("本页检查项目默认策略与演示模拟账户。使用自己的策略和几千元账户，请进入“我的策略每日复核”。")
    from dashboard.research_runner import jobs_panel
    from quant_lab.research.jobs import JobStore

    storage = JobStore(PROJECT_ROOT)
    if st.button("更新数据并检查调仓", type="primary", key="start_current_check"):
        try:
            storage.submit("update", {})
            st.info("更新任务已提交，完成后刷新状态。")
        except (ValueError, RuntimeError, OSError) as exc:
            st.error(str(exc))
    jobs_panel("update")
    status_path = PROJECT_ROOT / "data/state/current_check.json"
    updates = [s for s in storage.list() if s["kind"] == "update"]
    if updates and updates[0]["state"] != "succeeded":
        st.warning("本次更新尚未成功，暂不展示旧交易建议。请查看任务状态后重试。")
        return
    if not status_path.exists():
        st.info("尚未生成经过最新数据检查的结果。点击上方按钮开始。")
        return
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
        calendar_path = PROJECT_ROOT / "data/raw/baostock_supplement/reference/current_calendar.parquet"
        calendar = pd.read_parquet(calendar_path) if calendar_path.exists() else pd.DataFrame()
        current, message = current_result_status(status, calendar, ready_hour=int(status.get("data_ready_hour", 18)))
        config_path = PROJECT_ROOT / "configs/research.yaml"
        if current and status.get("config_sha256") != hashlib.sha256(config_path.read_bytes()).hexdigest():
            current, message = False, "研究配置已修改，请重新更新并计算"
        from quant_lab.research.service import code_version
        if current and status.get("code_version") != code_version(PROJECT_ROOT):
            current, message = False, "计算代码已变化，请重新更新并计算"
        if current:
            state_path = PROJECT_ROOT / status["paper_state_file"]
            state_hash = hashlib.sha256(state_path.read_bytes()).hexdigest() if state_path.exists() else None
            if state_hash != status.get("paper_state_sha256"):
                current, message = False, "模拟持仓已修改，请重新检查调仓建议"
    except (ValueError, OSError, KeyError):
        st.error("检查状态暂不可读，请刷新或重新运行。")
        return
    if not current:
        st.warning(message)
        st.caption("当前没有可用于本次判断的新建议；历史研究结果不代表最新行情。")
        if status.get("data_check"):
            with st.expander("查看数据检查结果"):
                st.json(status["data_check"])
        log_path = PROJECT_ROOT / "data/state/current_check.log"
        if log_path.exists():
            with st.expander("下载与运行进度"):
                st.code("\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]))
        return
    st.success(message)
    metrics = st.columns(4)
    metrics[0].metric("行情截止日", status["data_through"])
    metrics[1].metric("下一交易日", status["next_trade_date"])
    metrics[2].metric("股票池", str(status["data_check"]["research_symbols"]))
    labels = {"NO_TRADE": "无需调仓", "REVIEW_REQUIRED": "需人工复核", "UNFILLED": "资金或申报量不足"}
    metrics[3].metric("本次结果", labels.get(status["order_status"], status["order_status"]))
    st.caption(f"使用项目的模拟账户，现金 {status['paper_cash']:,.2f} 元；这不是已连接的券商账户。")
    output = (PROJECT_ROOT / status["report_dir"]).resolve()
    if not output.is_relative_to((PROJECT_ROOT / "reports/generated/current_check").resolve()):
        st.error("报告路径不合法")
        return
    orders = pd.read_csv(output / "next_day_orders.csv")
    signals = pd.read_csv(output / "latest_signal.csv")
    st.subheader("纸面调仓建议")
    st.dataframe(order_preview(orders), hide_index=True, width="stretch")
    st.caption("仍需核对实际持仓、资金及次日开盘状态，由你决定是否交易。")
    st.subheader("最新因子排名")
    st.dataframe(signals[["trade_date", "symbol", "factor_rank", "factor_processed", "close", "valid_factor_count"]].head(50), hide_index=True, width="stretch")
