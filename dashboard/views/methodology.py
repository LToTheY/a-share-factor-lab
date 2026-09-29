"""Read the project manual and beginner curriculum inside the dashboard."""
import re
from urllib.parse import quote

import streamlit as st

from dashboard.context import PROJECT_ROOT, page_intro


def render():
    page_intro("教程", "从运行项目到解释研究结论，每次完成一个约45分钟的小任务。")
    docs = PROJECT_ROOT / "docs"
    entries = {"项目使用教程": docs / "USER_GUIDE.md", "小白学习路线": docs / "BEGINNER_COURSE.md",
               "因子与策略研究方法": docs / "FACTOR_RESEARCH_METHOD.md", "自己的账户如何每日复核": docs / "MANUAL_REVIEW.md",
               "数据使用与更新": docs / "DATA_USAGE.md", "自定义因子": docs / "CUSTOM_FACTORS.md", "成交模型与限制": docs / "EXECUTION_ASSUMPTIONS.md"}
    entries.update({"课程 · " + p.stem: p for p in sorted((docs / "factor_course").glob("*.md"))})
    entries.update({"参考 · " + p.stem: p for p in sorted(docs.glob("*.md")) if p not in entries.values() and p.name != "SESSION_HANDOFF.md"})
    requested = st.query_params.get("guide")
    labels = list(entries)
    initial = next((i for i, label in enumerate(labels) if str(entries[label].relative_to(docs)).replace("\\", "/") == requested), 0)
    chosen = st.selectbox("选择教程或课程", labels, index=initial)
    path = entries[chosen]
    if path.is_file():
        def local_link(match):
            label, target = match.groups()
            if "://" in target or not target.endswith(".md"):
                return match.group(0)
            destination = (path.parent / target).resolve()
            if destination in entries.values():
                return f"[{label}](?guide={quote(destination.relative_to(docs).as_posix())})"
            return match.group(0)
        st.markdown(re.sub(r"\[([^\]]+)\]\(([^)]+)\)", local_link, path.read_text(encoding="utf-8")))
    else:
        st.info("教程正在整理，请先阅读数据使用说明。")
