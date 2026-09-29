"""Read the project manual and beginner curriculum inside the dashboard."""
import re
from urllib.parse import quote

import streamlit as st

from dashboard.context import PROJECT_ROOT, page_intro


def guide_id(path, docs):
    if path.is_relative_to(docs):
        return path.relative_to(docs).as_posix()
    return "../" + path.relative_to(PROJECT_ROOT).as_posix()


def render_document(path, entries, docs):
    def local_link(match):
        label, target = match.groups()
        document, separator, anchor = target.partition("#")
        if "://" in document or not document.endswith(".md"):
            return match.group(0)
        destination = (path.parent / document).resolve()
        if destination in entries.values():
            suffix = "#" + anchor if separator else ""
            return f"[{label}](?guide={quote(guide_id(destination, docs))}{suffix})"
        return match.group(0)

    content = path.read_text(encoding="utf-8")
    cursor = 0
    for match in re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", content):
        st.markdown(re.sub(r"\[([^\]]+)\]\(([^)]+)\)", local_link, content[cursor:match.start()]))
        caption, target = match.groups()
        if "://" in target:
            st.markdown(match.group(0))
            cursor = match.end()
            continue
        destination = (path.parent / target).resolve()
        if destination.is_relative_to(PROJECT_ROOT) and destination.is_file():
            if destination.suffix.lower() == ".svg":
                st.image(destination.read_text(encoding="utf-8"), caption=caption, width="stretch")
            else:
                st.image(str(destination), caption=caption, width="stretch")
        else:
            st.markdown(match.group(0))
        cursor = match.end()
    st.markdown(re.sub(r"\[([^\]]+)\]\(([^)]+)\)", local_link, content[cursor:]))


def render():
    page_intro("教程", "从运行项目到解释研究结论，每次完成一个约45分钟的小任务。")
    docs = PROJECT_ROOT / "docs"
    entries = {"项目使用教程": docs / "USER_GUIDE.md", "小白学习路线": docs / "BEGINNER_COURSE.md",
               "因子与策略研究方法": docs / "FACTOR_RESEARCH_METHOD.md", "自己的账户如何每日复核": docs / "MANUAL_REVIEW.md",
               "数据使用与更新": docs / "DATA_USAGE.md", "自定义因子": docs / "CUSTOM_FACTORS.md", "成交模型与限制": docs / "EXECUTION_ASSUMPTIONS.md"}
    entries.update({"课程 · " + p.stem: p for p in sorted((docs / "factor_course").glob("*.md"))})
    entries.update({"参考 · " + p.stem: p for p in sorted(docs.glob("*.md")) if p not in entries.values() and p.name != "SESSION_HANDOFF.md"})
    entries.update({"项目首页说明": PROJECT_ROOT / "README.md",
                    "合成演示图表": PROJECT_ROOT / "examples/synthetic_demo/README.md"})
    requested = st.query_params.get("guide")
    labels = list(entries)
    initial = next((i for i, label in enumerate(labels) if guide_id(entries[label], docs) == requested), 0)
    chosen = st.selectbox("选择教程或课程", labels, index=initial)
    path = entries[chosen]
    if path.is_file():
        render_document(path, entries, docs)
    else:
        st.info("教程正在整理，请先阅读数据使用说明。")
