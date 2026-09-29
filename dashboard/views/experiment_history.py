"""Saved backtest comparison without rerunning or tuning a strategy."""

import json

import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard.backtest_details import conditions, ledger
from dashboard.components import drawdown_figure
from dashboard.context import PROJECT_ROOT, page_intro
from quant_lab.dashboard import ArtifactError
from quant_lab.dashboard.experiments import ExperimentStore

EXPERIMENT_ROOT = PROJECT_ROOT / "data" / "state" / "strategy_experiments"


def _flatten(value, prefix=""):
    result = {}
    for key, item in value.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict):
            result.update(_flatten(item, name))
        else:
            result[name] = json.dumps(item, ensure_ascii=False)
    return result


def render():
    page_intro(
        "实验历史", "保留每次成功运行的条件与结果，比较改动，再回到具体交易日核对。"
    )
    storage = ExperimentStore(EXPERIMENT_ROOT)
    records, errors = storage.list()
    for error in errors:
        st.warning(error)
    if not records:
        st.info("还没有保存的实验。到策略实验室运行一次回测，成功后会自动保存在本机。")
        return
    lookup = {record["id"]: record for record in records}
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "实验": record["name"],
                    "保存时间（UTC）": record["created_at"],
                    "开始": record["actual_start"],
                    "结束": record["actual_end"],
                    "调仓": record["strategy"]["rebalance_frequency"],
                    "年化收益": record["metrics"].get("annual_return"),
                    "最大回撤": record["metrics"].get("max_drawdown"),
                }
                for record in records
            ]
        ),
        hide_index=True,
        width="stretch",
    )

    def label(identifier):
        record = lookup[identifier]
        return f"{record['name']} · {record['created_at']} · {identifier[:6]}"

    first = st.selectbox("查看实验", list(lookup), format_func=label)
    second = st.selectbox(
        "对比实验（可选）",
        [None, *[key for key in lookup if key != first]],
        format_func=lambda key: "不对比" if key is None else label(key),
    )
    metadata = lookup[first]
    from quant_lab.data.market_dataset import load_market_manifest
    from quant_lab.research.service import code_version
    source = metadata["provenance"]
    current_manifest = load_market_manifest(PROJECT_ROOT / source.get("market_file", "data/processed/market_daily.parquet"))
    if (source.get("code_version") != code_version(PROJECT_ROOT) or
            current_manifest.get("dataset_id") != source.get("dataset_id")):
        st.warning("历史版本实验：数据或代码与当前环境不同（也可能旧记录未绑定版本）。结果可查看，重跑前需核对或重建研究。")
    if source.get("provider") == "synthetic":
        st.info("本实验使用合成数据，只验证流程，不代表真实收益。")
    request = metadata["provenance"].get("request")
    if request and st.button("载入此实验参数到策略实验室"):
        st.session_state["reload_strategy"] = request
        st.session_state["report_dir"] = str(PROJECT_ROOT / request["report"])
        st.success("已载入。进入策略实验室检查参数，然后运行新实验。")
    st.write("研究假设：", metadata["provenance"].get("hypothesis", "旧实验未记录"))
    notes = storage.notes(first)
    conclusion = st.text_area("研究结论与失败复盘", notes.get("conclusion", metadata["provenance"].get("conclusion", "")), key="conclusion_" + first)
    if st.button("保存结论", key="save_conclusion_" + first):
        storage.save_notes(first, conclusion)
        st.success("结论已保存；原始实验参数和结果保持不可变。")
    validation = metadata["provenance"].get("validation")
    if isinstance(validation, dict):
        st.subheader("本实验样本外验证")
        st.write(validation.get("message") or validation.get("method"))
        if validation.get("folds"):
            st.dataframe(validation["folds"], hide_index=True)
    if metadata["provenance"].get("robustness"):
        st.subheader("全部单因素对照（未按收益择优）")
        st.dataframe(metadata["provenance"]["robustness"], hide_index=True)
    diagnostics = metadata.get("diagnostics", {})
    cards = st.columns(4)
    cards[0].metric("平均实际持股数", f"{diagnostics.get('average_holdings', 0):.2f}")
    cards[1].metric("平均现金闲置", f"{diagnostics.get('average_cash_ratio', 0):.1%}")
    cards[2].metric("费用 / 初始资金", f"{diagnostics.get('cost_to_initial_cash', 0):.1%}")
    cards[3].metric("未成交事件", diagnostics.get("unfilled_events", 0))
    report = f"# {metadata['name']}\n\n假设：{metadata['provenance'].get('hypothesis', '')}\n\n结论：{conclusion}\n\n```json\n{json.dumps(metadata, ensure_ascii=False, indent=2)}\n```\n\n分红送转账本近似，历史回测不代表未来收益。"
    st.download_button("导出实验报告", report, file_name=f"experiment_{first}.md", mime="text/markdown")
    conditions(metadata)
    try:
        equity = storage.table(first, "equity")
        if second:
            other = lookup[second]
            st.subheader("参数差异")
            a = _flatten(
                {key: metadata[key] for key in ("strategy", "backtest", "provenance")}
            )
            b = _flatten(
                {key: other[key] for key in ("strategy", "backtest", "provenance")}
            )
            differences = [
                {
                    "参数": key,
                    "当前实验": a.get(key, "未记录"),
                    "对比实验": b.get(key, "未记录"),
                }
                for key in sorted(a.keys() | b.keys())
                if a.get(key) != b.get(key)
            ]
            if differences:
                st.dataframe(
                    pd.DataFrame(differences), hide_index=True, width="stretch"
                )
            else:
                st.info("记录的参数与数据来源一致。")
            labels = {
                "annual_return": "年化收益",
                "total_return": "总收益",
                "max_drawdown": "最大回撤",
                "sharpe": "Sharpe",
                "turnover": "累计换手",
            }
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "指标": label,
                            "当前实验": metadata["metrics"].get(key),
                            "对比实验": other["metrics"].get(key),
                        }
                        for key, label in labels.items()
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
            comparable = all(
                metadata.get(key) == other.get(key)
                for key in ("actual_start", "actual_end")
            )
            same_data = all(
                metadata["provenance"].get(key) == other["provenance"].get(key)
                and metadata["provenance"].get(key) is not None
                for key in ("market_fingerprint", "score_fingerprint")
            )
            if not comparable or not same_data:
                st.warning(
                    "样本区间或输入数据指纹不同；指标仅供核对，不能把差异直接归因于策略参数。"
                )
            if comparable:
                curves = []
                for identifier, data in (
                    (first, equity),
                    (second, storage.table(second, "equity")),
                ):
                    curve = data[["trade_date", "equity"]].copy()
                    curve["净值"] = (
                        curve.equity / lookup[identifier]["backtest"]["initial_cash"]
                    )
                    curve["实验"] = label(identifier)
                    curves.append(curve)
                st.plotly_chart(
                    px.line(pd.concat(curves), x="trade_date", y="净值", color="实验"),
                    width="stretch",
                )
        else:
            curve = equity.assign(
                净值=equity.equity / metadata["backtest"]["initial_cash"]
            )
            st.plotly_chart(px.line(curve, x="trade_date", y="净值"), width="stretch")
        st.plotly_chart(drawdown_figure(equity), width="stretch")
        ledger(
            equity,
            storage.table(first, "targets"),
            storage.table(first, "trades"),
            storage.table(first, "positions"),
            storage.table(first, "execution_issues"),
            key=f"history_day_{first}",
        )
        st.download_button(
            "下载本次实验配置与来源",
            json.dumps(metadata, ensure_ascii=False, indent=2),
            file_name=f"experiment_{first}.json",
            mime="application/json",
        )
    except (ArtifactError, ValueError, KeyError) as exc:
        st.error(f"实验结果不可用：{exc}")
    st.caption(
        "历史记录保存参数与结果及输入指纹，不包含完整行情副本；重新计算仍需相同输入数据。"
    )
