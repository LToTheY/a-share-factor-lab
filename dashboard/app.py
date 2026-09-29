"""Local Streamlit research workspace for A-Share Factor Lab."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for path in (PROJECT_ROOT, SRC_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from dashboard.context import DEFAULT_REPORT_DIR
from dashboard.views import (
    backtest,
    current_check,
    experiment_history,
    factor_library,
    factors,
    methodology,
    overview,
    strategy_lab,
    walk_forward,
)

st.set_page_config(
    page_title="A股因子研究看板",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .block-container {padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1500px;}
    [data-testid="stSidebar"] {border-right: 1px solid rgba(128,128,128,.15);}
    h1, h2, h3 {letter-spacing: -0.025em;}
    [data-testid="stMetric"] {
        background: rgba(128, 128, 128, 0.07);
        border: 1px solid rgba(128, 128, 128, 0.16);
        border-radius: 0.75rem;
        padding: 0.8rem 1rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.session_state.setdefault("report_dir", str(DEFAULT_REPORT_DIR))

from dashboard import research_runner

pages = {
    "1 · 数据检查": [
        st.Page(overview.render, title="系统总览", icon="🏠", url_path="overview", default=True),
        st.Page(current_check.render, title="今日调仓检查", icon="🔄", url_path="current-check"),
    ],
    "2 · 因子研究": [
        st.Page(research_runner.render, title="运行因子研究", icon="▶️", url_path="run-research"),
        st.Page(factor_library.render, title="因子库", icon="🗂️", url_path="factor-library"),
        st.Page(factors.render, title="因子诊断", icon="🧪", url_path="factors"),
        st.Page(factor_library.daily, title="日频因子", url_path="daily-factors"),
        st.Page(factor_library.intraday, title="日内档案（尚不可计算）", url_path="intraday-factors"),
        st.Page(factor_library.other, title="其他频率档案", url_path="other-factors"),
    ],
    "3 · 策略实验": [
        st.Page(strategy_lab.render, title="策略实验室", icon="🛠️", url_path="strategy-lab"),
        st.Page(backtest.render, title="研究报告回测", icon="📈", url_path="backtest"),
        st.Page(walk_forward.render, title="研究报告样本外", icon="🧭", url_path="walk-forward"),
    ],
    "4 · 实验历史": [st.Page(experiment_history.render, title="实验历史与比较", icon="🗃️", url_path="experiment-history")],
    "5 · 教程": [st.Page(methodology.render, title="使用教程与45分钟课程", icon="📚", url_path="methodology")],
}

with st.sidebar:
    st.markdown("### A股因子实验室")
    st.caption("积累因子 · 验证想法 · 组合策略")
    st.caption("日频研究已接入，日内及其他频率支持建档。")
    st.divider()
    st.caption(f"报告目录\n\n`{st.session_state['report_dir']}`")

navigation = st.navigation(pages, position="sidebar")
navigation.run()
