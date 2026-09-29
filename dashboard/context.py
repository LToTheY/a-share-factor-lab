"""Shared dashboard runtime context."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from quant_lab.dashboard import ArtifactStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports" / "generated" / "daily_factor_lab"
DEFAULT_MARKET_FILE = PROJECT_ROOT / "data" / "processed" / "market_daily.parquet"


@st.cache_resource
def get_store(report_dir: str) -> ArtifactStore:
    return ArtifactStore(report_dir)


def store() -> ArtifactStore:
    report_dir = st.session_state.get("report_dir", str(DEFAULT_REPORT_DIR))
    return get_store(report_dir)


def page_intro(title: str, description: str) -> None:
    st.title(title)
    st.caption(description)
    st.caption("本地研究工作区 · 支持因子建档与沙盒实验 · 不会发送真实交易指令")
