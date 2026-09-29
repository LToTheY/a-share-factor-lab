"""Frequency-aware factor library and local research notebooks."""

from __future__ import annotations

import json
import sqlite3

import streamlit as st

from dashboard.context import PROJECT_ROOT, page_intro, store
from dashboard.custom_factors import render_python_factors
from dashboard.views import factors
from dashboard.views.factors import FACTOR_NOTES
from quant_lab.dashboard import ArtifactError
from quant_lab.dashboard.catalog import (
    CATEGORIES,
    FREQUENCIES,
    INTERVALS,
    STAGES,
    FactorCatalog,
    category_for,
)
from quant_lab.factors.custom_loader import custom_factor_metadata
from quant_lab.factors.library import FACTOR_REGISTRY

CATALOG_PATH = PROJECT_ROOT / "data" / "state" / "factor_catalog.sqlite3"
FACTOR_TITLES = {
    "momentum_20_5": "短期动量 · 20日",
    "momentum_60_5": "中期动量 · 60日",
    "momentum_120_20": "长期动量 · 120日",
    "reversal_5": "短期反转 · 5日",
    "reversal_20": "月度反转 · 20日",
    "volatility_20": "收益波动率 · 20日",
    "volatility_60": "收益波动率 · 60日",
    "amihud_20": "非流动性 · Amihud",
    "turnover_mean_20": "平均换手率 · 20日",
    "amount_momentum_20": "成交额动量 · 20日",
    "price_volume_corr_20": "量价相关性 · 20日",
    "ivol_60": "特质波动率 · 60日",
    "book_to_price": "账面市值比 · BP",
    "earnings_yield": "盈利收益率 · EP",
}


def _editor(catalog: FactorCatalog, frequency: str, record: dict | None = None):
    current = record or {}
    editing = record is not None
    identity = f"{frequency}_{current.get('interval', '')}_{current.get('name', 'new')}"
    with st.form(f"factor_form_{identity}"):
        left, right = st.columns(2)
        name = left.text_input(
            "因子标识",
            value=current.get("name", ""),
            placeholder="例如 intraday_reversal",
            disabled=editing,
        )
        title = right.text_input("显示名称", value=current.get("title", ""))
        left, middle, right = st.columns(3)
        intervals = INTERVALS[frequency]
        interval = left.selectbox(
            "采样周期",
            intervals,
            index=intervals.index(current.get("interval", intervals[0])),
            disabled=editing,
        )
        category = middle.selectbox(
            "因子分类",
            CATEGORIES,
            index=CATEGORIES.index(current.get("category", "其他")),
        )
        stage = right.selectbox(
            "研究阶段", STAGES, index=STAGES.index(current.get("stage", "待研究"))
        )
        definition = st.text_area(
            "公式 / 研究假设",
            value=current.get("definition", ""),
            placeholder="记录计算定义、输入字段和预期方向。",
        )
        notes = st.text_area(
            "研究备注",
            value=current.get("notes", ""),
            placeholder="数据来源、信号可见时间、验证结果、下一步。",
        )
        st.caption("保存的是研究档案；公式不会作为代码执行，也不会自动加入策略。")
        submitted = st.form_submit_button(
            "保存修改" if editing else "保存因子", type="primary"
        )
        if submitted:
            try:
                if frequency == "daily" and name.strip() in FACTOR_REGISTRY:
                    raise ValueError(
                        "该标识属于可执行日频因子，请为研究档案使用新标识。"
                    )
                catalog.save(
                    {
                        "frequency": frequency,
                        "interval": interval,
                        "name": name,
                        "title": title,
                        "category": category,
                        "stage": stage,
                        "definition": definition,
                        "notes": notes,
                    },
                    replace=editing,
                )
            except (ValueError, OSError, sqlite3.Error) as exc:
                st.error(f"保存失败：{exc}")
            else:
                st.session_state["catalog_notice"] = (
                    "因子档案已保存，刷新或重启后仍可查看。"
                )
                st.rerun()


def render(frequency: str | None = None) -> None:
    page_intro("因子库", "把研究想法、因子定义和验证入口放在一起，按频率逐步积累。")
    code_metadata = custom_factor_metadata()
    catalog = FactorCatalog(CATALOG_PATH)
    try:
        records = catalog.records()
    except (OSError, sqlite3.Error) as exc:
        st.error(f"无法读取因子档案：{exc}")
        return
    notice = st.session_state.pop("catalog_notice", None)
    if notice:
        st.success(notice)
    for column, (key, label) in zip(st.columns(3), FREQUENCIES.items()):
        with column.container(border=True):
            count = sum(item["frequency"] == key for item in records)
            if key == "daily":
                count += len(FACTOR_REGISTRY)
            st.metric(f"{label}因子", count)
            st.caption(
                {
                    "daily": "已有日线研究链路",
                    "intraday": "分钟 / Tick · 支持建档",
                    "other": "周频 / 月频 / 事件 · 支持建档",
                }[key]
            )
    if frequency is None:
        frequency = st.radio(
            "研究频率", list(FREQUENCIES), format_func=FREQUENCIES.get, horizontal=True
        )
    st.subheader(f"{FREQUENCIES[frequency]}工作区")
    if frequency != "daily":
        st.info("此入口已支持保存与管理因子档案；对应数据、因子计算和回测尚未接入。")
    else:
        st.caption("这里按因子采样频率归类。日频因子仍可用于每周调仓策略。")
        render_python_factors()
    with st.expander("＋ 新建因子档案"):
        _editor(catalog, frequency)
    left, right = st.columns([3, 1])
    query = (
        left.text_input("搜索因子", placeholder="输入名称、标识或研究关键词")
        .strip()
        .lower()
    )
    category = right.selectbox("分类筛选", ["全部", *CATEGORIES])
    selected = [
        dict(item, builtin=False) for item in records if item["frequency"] == frequency
    ]
    available = set()
    if frequency == "daily":
        try:
            available = set(store().factor_names())
        except ArtifactError:
            st.caption("暂无可读的日频研究报告；内置定义和自定义档案仍可浏览。")
        selected = [
            {
                "name": name,
                "title": code_metadata.get(name, {}).get("title", FACTOR_TITLES.get(name, name)),
                "interval": "1d",
                "category": category_for(name),
                "definition": code_metadata.get(name, {}).get("description") or FACTOR_NOTES.get(
                    name, "已实现计算函数，详见因子库源码。"
                ),
                "stage": ("代码已加载 · 研究结果需另行生成" if name in code_metadata else
                          "已有诊断" if name in available else "已实现 · 暂无诊断"),
                "notes": (f"Python 文件：{code_metadata[name]['path']}" if name in code_metadata else ""),
                "builtin": True,
            }
            for name in FACTOR_REGISTRY
        ] + selected
    selected = [
        item
        for item in selected
        if (category == "全部" or item["category"] == category)
        and query in " ".join(str(value) for value in item.values()).lower()
    ]
    st.caption(f"显示 {len(selected)} 个因子")
    if not selected:
        st.info("还没有匹配的因子。可清空筛选，或在上方新建档案。")
    columns = st.columns(2)
    for index, item in enumerate(selected):
        with columns[index % 2].container(border=True):
            st.subheader(item["title"])
            st.caption(
                f"{item['name']} · {item['interval']} · {item['category']} · {item['stage']}"
            )
            st.write(item["definition"])
            if item["notes"]:
                st.write(item["notes"])
            if item["name"] in code_metadata:
                with st.expander("查看已加载的 Python 源码"):
                    st.code(code_metadata[item["name"]]["source"], language="python")
                if item["name"] in available:
                    st.caption("已有同名报告；修改代码后需重新研究，同名不代表同一版本。")
            if item["builtin"] and item["name"] in available:
                if st.button("查看日频诊断 →", key=f"diagnose_{item['name']}"):
                    st.session_state["selected_factor"] = item["name"]
                    st.switch_page(
                        st.Page(factors.render, title="因子研究", url_path="factors")
                    )
            elif not item["builtin"]:
                with st.expander("编辑档案"):
                    _editor(catalog, frequency, item)
    st.download_button(
        "导出全部自定义档案（JSON）",
        json.dumps(records, ensure_ascii=False, indent=2),
        file_name="factor_catalog.json",
        mime="application/json",
    )
    st.caption("档案仅保存在本机，不随每日研究报告覆盖；可导出备份。")


def daily():
    render("daily")


def intraday():
    render("intraday")


def other():
    render("other")
