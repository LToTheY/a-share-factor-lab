"""Walk-forward, out-of-sample diagnostics."""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from dashboard.components import equity_figure, number, pct
from dashboard.context import page_intro, store
from quant_lab.dashboard import ArtifactError


def render() -> None:
    page_intro("Walk-forward 样本外检验", "每一折只使用当时可见的训练和验证数据，再检查下一段未知数据。")
    artifacts = store()
    try:
        summary = artifacts.json("walk_forward/summary.json")
        folds = artifacts.csv("walk_forward/fold_summary.csv")
        ic = artifacts.csv("walk_forward/oos_ic.csv")
        strategy = artifacts.csv("walk_forward/equity.csv")
        benchmark = artifacts.csv("walk_forward/benchmark_equity.csv")
    except ArtifactError as exc:
        st.error(str(exc))
        return

    ic_summary = summary.get("ic", {})
    portfolio = summary.get("portfolio", {})
    cols = st.columns(6)
    cols[0].metric("状态", summary.get("status", "—"))
    cols[1].metric("样本外折数", number(summary.get("folds"), 0))
    cols[2].metric("OOS Mean IC", number(ic_summary.get("mean_ic"), 4))
    cols[3].metric("OOS ICIR", number(ic_summary.get("icir"), 2))
    cols[4].metric("OOS 年化", pct(portfolio.get("annual_return")))
    cols[5].metric("OOS 最大回撤", pct(portfolio.get("max_drawdown")))

    st.plotly_chart(
        equity_figure(strategy, benchmark, "样本外策略与基准"),
        width="stretch",
    )
    st.plotly_chart(
        px.line(ic, x="trade_date", y="rank_ic", title="样本外每日 Rank IC"),
        width="stretch",
    )

    st.subheader("每折选择结果")
    display = folds.copy()
    rename = {
        "test_year": "测试年份",
        "train_start_year": "训练起始年",
        "validation_start_year": "验证年份",
        "selected_factors": "所选因子",
        "selected_count": "因子数",
        "test_mean_ic": "测试期 Mean IC",
    }
    st.dataframe(display.rename(columns=rename), width="stretch", hide_index=True)

    benchmark_metrics = summary.get("benchmark", {})
    if portfolio.get("annual_return", 0) < benchmark_metrics.get("annual_return", 0):
        st.warning(
            "样本外组合年化收益低于基准。虽然样本外 IC 为正，"
            "但预测相关性没有自动转化为组合超额收益，需要继续研究组合构造和成本。"
        )
    st.markdown(
        f"测试区间：`{summary.get('start_date', '—')}` 至 `{summary.get('end_date', '—')}`；"
        f"训练与测试之间留出 `{summary.get('embargo_trading_days', '—')}` 个交易日隔离期。"
    )
