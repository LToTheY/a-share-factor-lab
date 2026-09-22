"""Learning map and interpretation guide."""

from __future__ import annotations

import streamlit as st

from dashboard.context import PROJECT_ROOT, page_intro


def render() -> None:
    page_intro("方法与课程", "把网页上的指标对应回项目课程和源码，做到能够解释、修改和复现。")

    st.subheader("建议学习顺序")
    lessons = [
        ("00", "怎么学习这套项目", "先建立地图，不急着背公式"),
        ("01", "因子研究时间线", "区分信号日、执行日和未来收益"),
        ("02", "看懂一行行情", "理解 DataFrame 中每一列"),
        ("03", "亲手计算动量", "从价格得到一个因子值"),
        ("04", "预处理", "去极值、标准化和方向调整"),
        ("05", "判断预测能力", "理解 Rank IC、ICIR 和胜率"),
        ("06", "多因子合成", "从多个因子得到综合排名"),
        ("07", "组合构造", "从排名变成目标权重"),
        ("08", "回测", "模拟次日执行、成本和约束"),
        ("09", "Walk-forward", "只用当时可见数据做样本外检验"),
        ("10", "挖掘新因子", "提出假设并完成稳健性检查"),
    ]
    st.dataframe(
        [{"课程": code, "主题": title, "学习目标": goal} for code, title, goal in lessons],
        width="stretch",
        hide_index=True,
    )

    st.subheader("页面和课程的对应关系")
    st.markdown(
        """
        - **因子研究页**：第 03～06 课，重点看方向、IC、稳定性和相关性。
        - **回测分析页**：第 07～08 课，重点看执行时点、成本、换手与回撤。
        - **样本外检验页**：第 09～10 课，重点避免样本内挑参数和未来信息泄漏。
        """
    )

    st.subheader("三个必须区分的结论")
    st.markdown(
        """
        1. **已计算事实**：例如本次报告中的 Mean IC、收益和回撤。
        2. **研究解释**：例如 IC 为正可能表示排序方向具有预测信息。
        3. **待验证假设**：例如降低换手是否能改善样本外收益，必须重新实验后判断。
        """
    )

    course_dir = PROJECT_ROOT / "docs" / "factor_course"
    st.caption(f"课程文件保存在：{course_dir}")
    st.code(".\\.venv\\Scripts\\python.exe -m streamlit run dashboard\\app.py", language="powershell")
