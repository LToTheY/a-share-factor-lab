"""Local, read-only Streamlit entry point for A-Share Factor Lab."""

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
    factors,
    methodology,
    overview,
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
    .block-container {padding-top: 1.6rem; padding-bottom: 3rem;}
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

pages = {
    "研究看板": [
        st.Page(
            overview.render,
            title="系统总览",
            icon="🏠",
            url_path="overview",
            default=True,
        ),
        st.Page(factors.render, title="因子研究", icon="🧪", url_path="factors"),
        st.Page(backtest.render, title="回测分析", icon="📈", url_path="backtest"),
        st.Page(
            walk_forward.render,
            title="样本外检验",
            icon="🧭",
            url_path="walk-forward",
        ),
    ],
    "学习与说明": [
        st.Page(
            methodology.render,
            title="方法与课程",
            icon="📚",
            url_path="methodology",
        ),
    ],
}

with st.sidebar:
    st.markdown("### A股因子实验室")
    st.caption("读取现有研究产物，不在网页中训练模型或执行交易。")
    st.divider()
    st.caption(f"报告目录\n\n`{DEFAULT_REPORT_DIR}`")

navigation = st.navigation(pages, position="sidebar")
navigation.run()
