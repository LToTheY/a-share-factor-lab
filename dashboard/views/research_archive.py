"""Show complete predeclared studies, including unfinished and negative cases."""

import json

import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard.context import PROJECT_ROOT, page_intro
from quant_lab.research.service import code_version, local_path

POOL_NAMES = {"000300.SH": "沪深300", "000905.SH": "中证500", "CSI800": "两者并集"}


def campaign_table(state):
    rows = []
    for plan in state.get("plan", []):
        case = state.get("cases", {}).get(plan["key"], {})
        metrics, diagnostics = case.get("metrics", {}), case.get("diagnostics", {})
        rows.append({"股票池": POOL_NAMES.get(plan["pool"], plan["pool"]), "因子": plan["factor"],
                     "选股方式": "预算筛选" if plan["selection_mode"] == "affordable" else "纯排名",
                     "状态": "完成" if case.get("status") == "succeeded" else "未完成",
                     "年化收益（%）": metrics.get("annual_return", float("nan")) * 100,
                     "最大回撤（%）": metrics.get("max_drawdown", float("nan")) * 100,
                     "现金闲置（%）": diagnostics.get("average_cash_ratio", float("nan")) * 100,
                     "费用/初始资金（%）": diagnostics.get("cost_to_initial_cash", float("nan")) * 100,
                     "实际平均持股": diagnostics.get("average_holdings", float("nan")),
                     "实验编号": case.get("experiment_id", "")})
    return pd.DataFrame(rows)


def render():
    page_intro("批量研究档案", "按事先声明的顺序保留全部对照，观察失败与执行限制，不自动推荐收益最高的方案。")
    directory = PROJECT_ROOT / "reports/generated/campaigns"
    entries = sorted(directory.glob("*/campaign.json"), reverse=True)
    if not entries:
        st.info("尚无批量研究档案。可以先在策略实验室保存两个只改变一个参数的实验，再到实验历史比较。")
        return
    selected = st.selectbox("选择研究批次", entries, format_func=lambda path: path.parent.name)
    try:
        state = json.loads(selected.read_text(encoding="utf-8"))
        frame = campaign_table(state)
        if frame.empty:
            st.info("此批次尚无声明的实验案例。")
            return
        complete = frame["状态"].eq("完成")
        st.metric("完成案例", f"{complete.sum()} / {len(frame)}")
        st.caption(f"数据截止：{state.get('dataset', {}).get('end_date', '未记录')}。{state.get('interpretation', '')}")
        if state.get("reason"):
            st.warning(f"批次停止原因：{state['reason']}。未完成案例没有结果，不按0收益计入。")
        if state.get("code_version") != code_version(PROJECT_ROOT):
            st.warning("这是历史代码版本的研究档案。结果保留供复盘；新实验需要使用当前版本重新验证。")
        st.warning("原始价格账本尚未完整计入分红送转与退市清算；下列收益含模型近似，不是实际账户收益。")
        formats = {name: st.column_config.NumberColumn(format="%.2f") for name in frame if "（%）" in name or name == "实际平均持股"}
        st.dataframe(frame, hide_index=True, width="stretch", column_config=formats)
        chart = frame.loc[complete].dropna(subset=["年化收益（%）", "现金闲置（%）"])
        if not chart.empty:
            figure = px.scatter(chart, x="现金闲置（%）", y="年化收益（%）", color="股票池", symbol="选股方式",
                                hover_data=["因子", "费用/初始资金（%）", "最大回撤（%）"],
                                title="资金用得更多，结果是否真的更好？")
            figure.add_hline(y=0, line_dash="dot")
            st.plotly_chart(figure, width="stretch")
            st.caption("图只显示已完成案例。预算筛选同时改变价格、流动性和风格暴露，不能把差异全归因于现金利用率。")
        pools = list(state.get("reports", {}))
        if pools:
            pool = st.selectbox("查看该批次的因子诊断", pools, format_func=lambda value: POOL_NAMES.get(value, value))
            if st.button("打开该股票池的研究报告"):
                report = local_path(PROJECT_ROOT, state["reports"][pool]["path"], area="reports")
                if not (report / "summary.json").is_file():
                    raise ValueError("该报告尚未完成或文件缺失")
                st.session_state["report_dir"] = str(report)
                st.success("已切换报告，请进入因子诊断查看；旧版本提示会一并保留。")
        report_path = selected.parent / "RESEARCH_REPORT.md"
        if report_path.is_file():
            report = report_path.read_text(encoding="utf-8")
            st.download_button("导出完整复盘报告", report, file_name=f"{selected.parent.name}.md", mime="text/markdown")
            with st.expander("完整报告、分年度诊断与全部样本外折"):
                st.markdown(report)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        st.error(f"研究档案暂不可读：{exc}。原文件保留，请检查批次记录。")
