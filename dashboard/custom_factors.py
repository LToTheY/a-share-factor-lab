"""Local Python-factor update controls and a copyable in-app tutorial."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import streamlit as st
import yaml

from dashboard.context import DEFAULT_REPORT_DIR, PROJECT_ROOT
from quant_lab.factors import custom_loader


def research_config(base: dict, selected: list[str], metadata: dict) -> dict:
    """Build an isolated config without editing the daily signal configuration."""
    if not selected:
        raise ValueError("请至少选择一个代码因子")
    result = deepcopy(base)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    output = f"reports/generated/custom_factors/{stamp}"
    result["project"]["output_dir"] = output
    result["factors"]["definitions"] = [
        {"name": name, "direction": metadata[name]["direction"]} for name in selected
    ]
    result["factors"]["minimum_valid_factors"] = len(selected)
    result["paper_account"]["next_orders_file"] = f"{output}/next_day_orders.csv"
    return result


def render_python_factors() -> None:
    st.subheader("Python 自定义因子")
    st.caption("保存 .py 文件后点击更新。只加载代码，不会自动重跑回测或改变每日策略。")
    st.code(str(custom_loader.CUSTOM_DIR), language="text")
    if st.button("更新代码因子", key="refresh_python_factors", type="primary"):
        with st.spinner("加载并校验本地代码因子…"):
            rows = custom_loader.refresh_custom_factors()
        count = sum(row["status"] == "已加载" for row in rows)
        st.session_state["python_factor_notice"] = (
            f"更新完成：{count} 个已加载，{len(rows) - count} 个失败。"
        )
        st.rerun()
    notice = st.session_state.pop("python_factor_notice", None)
    if notice:
        st.success(notice)
    rows = custom_loader.custom_factor_results()
    if rows:
        st.dataframe(
            rows,
            hide_index=True,
            width="stretch",
            column_config={
                "file": "文件",
                "name": "因子标识",
                "status": "状态",
                "detail": "校验结果",
            },
        )
        for row in rows:
            if row["status"] == "失败":
                st.error(f"{row['file']}：{row['detail']}")
    else:
        st.info("还没有代码因子。展开下方教程，复制模板并保存为新的 .py 文件。")
    with st.expander("教程：因子写什么文件、模板怎么填、如何更新和回测"):
        st.markdown(
            (PROJECT_ROOT / "docs/CUSTOM_FACTORS.md").read_text(encoding="utf-8")
        )
        template = (
            PROJECT_ROOT / "src/quant_lab/factors/custom/_template.py"
        ).read_text(encoding="utf-8")
        st.code(template, language="python")
        st.download_button(
            "下载 Python 因子模板",
            template,
            file_name="my_momentum.py",
            mime="text/x-python",
        )
    metadata = custom_loader.custom_factor_metadata()
    if metadata:
        with st.expander("进入真实数据研究：下载独立配置"):
            selected = st.multiselect(
                "参加研究的代码因子", sorted(metadata), key="python_research_factors"
            )
            if selected:
                base = yaml.safe_load(
                    (PROJECT_ROOT / "configs/research.yaml").read_text(encoding="utf-8")
                )
                config = research_config(base, selected, metadata)
                st.download_button(
                    "下载独立研究配置",
                    yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
                    file_name="custom_research.yaml",
                    mime="text/yaml",
                )
                st.caption(
                    "保存至项目 configs/custom_research.yaml；在项目根目录运行："
                )
                st.code(
                    r".\.venv\Scripts\python.exe scripts\run_factor_suite.py --config configs/custom_research.yaml",
                    language="powershell",
                )
                st.caption(
                    "完成后，在下方选择生成的研究报告并打开。旧报告不代表新代码。"
                )
    with st.expander("打开研究结果：用于因子诊断、策略实验和回测"):
        root = PROJECT_ROOT / "reports/generated/custom_factors"
        reports = [
            DEFAULT_REPORT_DIR,
            *sorted(
                (
                    path.parent
                    for path in root.glob("*/summary.json")
                    if (path.parent / "factor_scores.parquet").is_file()
                ),
                reverse=True,
            ),
        ]
        chosen = st.selectbox(
            "研究报告",
            reports,
            format_func=lambda path: (
                "默认研究报告" if path == DEFAULT_REPORT_DIR else path.name
            ),
            key="custom_factor_report",
        )
        if st.button("打开所选研究报告", key="open_custom_factor_report"):
            st.session_state["report_dir"] = str(chosen)
            st.success("已切换。可进入因子研究、策略实验室或回测分析页面。")
        st.caption(
            f"当前报告：{st.session_state.get('report_dir', str(DEFAULT_REPORT_DIR))}"
        )
