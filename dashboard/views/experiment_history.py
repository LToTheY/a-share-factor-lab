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
    all_count = len(records)
    filters = st.columns(3)
    keyword = filters[0].text_input("查找实验名称或假设", key="experiment_search").strip().lower()
    provider_labels = {"全部来源": None, "真实历史数据": "csmar_baostock", "合成演示": "synthetic"}
    provider = filters[1].selectbox("实验数据来源", list(provider_labels), key="experiment_provider")
    pools = sorted({str(r["provenance"].get("universe", "未记录")) for r in records})
    pool = filters[2].selectbox("筛选股票池", ["全部股票池", *pools], key="experiment_pool")
    records = [r for r in records if (not keyword or keyword in (r["name"] + " " + str(r["provenance"].get("hypothesis", ""))).lower())
               and (provider_labels[provider] is None or r["provenance"].get("provider") == provider_labels[provider])
               and (pool == "全部股票池" or str(r["provenance"].get("universe", "未记录")) == pool)]
    st.caption(f"显示 {len(records)} / {all_count} 个已保存实验；筛选只影响显示，不删除历史结果。")
    if not records:
        st.info("没有符合条件的实验，请清空关键词或调整筛选。")
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
    with st.expander("复现代码与运行环境"):
        runtime = source.get("runtime")
        if runtime:
            st.json(runtime)
            if source.get("factor_runtime") and source["factor_runtime"] != runtime:
                st.caption("因子分数由另一套数值库环境生成，原记录如下；重跑时应分别核对。")
                st.json(source["factor_runtime"])
        else:
            st.caption("这份历史实验没有记录运行库版本，不能补造当时的环境。")
        from quant_lab.research.snapshots import snapshot_path, verify_snapshot
        try:
            path = snapshot_path(PROJECT_ROOT, source.get("code_version", ""))
            if path.is_file():
                manifest = verify_snapshot(path, source["code_version"])
                st.caption(f"已核验{len(manifest['files'])}份计算源码。配置另行导出；快照不含行情和账户。恢复时使用独立目录，避免覆盖当前项目。")
                st.download_button("下载该计算版本源码", path.read_bytes(), file_name=path.name, mime="application/zip", key="source_"+first)
            else:
                st.caption("此历史版本没有保存源码快照；仅有指纹不等于可以完整恢复。")
        except (ValueError, OSError) as exc:
            st.caption(f"此实验的源码快照不可用：{exc}")
    request = metadata["provenance"].get("request")
    if request and st.button("载入此实验参数到策略实验室"):
        from quant_lab.research.manual_review import resolve_report
        try:
            resolved = resolve_report(PROJECT_ROOT, source)
            request = {**request, "report": str(resolved.relative_to(PROJECT_ROOT))}
            st.session_state["reload_strategy"] = request
            st.session_state["report_dir"] = str(resolved)
            st.success("已载入匹配的研究快照。进入策略实验室检查参数，然后运行新实验。")
        except (ValueError, OSError) as exc:
            st.error(f"无法重开参数：{exc}。历史结果仍可查看。")
    st.write("研究假设：", metadata["provenance"].get("hypothesis", "旧实验未记录"))
    try:
        notes = storage.notes(first)
    except (ValueError, OSError) as exc:
        st.warning(f"结论文件不可读，原文件保留：{exc}")
        notes = {}
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
    if diagnostics.get("stale_valuation_events", 0):
        st.warning(f"有 {diagnostics['stale_valuation_events']} 条持仓估值沿用了上次有效价格。退市或长期缺行情可能使曲线失真，请在逐日回放核对后再评价收益。")
    if diagnostics.get("corporate_action_events", 0):
        st.warning(f"持仓期间检测到 {diagnostics['corporate_action_events']} 次除权息参考价变化。当前现金/股份账本尚未完整入账相关事件，收益、回撤与对照均包含误差；详见逐日回放。")
    cards = st.columns(4)
    cards[0].metric("平均实际持股数", f"{diagnostics.get('average_holdings', 0):.2f}")
    cards[1].metric("平均现金闲置", f"{diagnostics.get('average_cash_ratio', 0):.1%}")
    cards[2].metric("费用 / 初始资金", f"{diagnostics.get('cost_to_initial_cash', 0):.1%}")
    cards[3].metric("未成交事件", diagnostics.get("unfilled_events", 0))
    st.caption("实际持仓可能少于目标；若旧仓卖出受阻，也可能暂时多于Top N。现金闲置、持仓数与费用须结合成交记录解释。")
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
            if metadata["provenance"].get("code_version") != other["provenance"].get("code_version"):
                st.warning("两个实验使用的计算代码版本不同，收益差异也可能来自成交模型或计算逻辑变化。")
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
        audit = storage.table(first, "selection_audit")
        if not audit.empty:
            with st.expander("为什么选中或跳过某只股票"):
                audit_dates = sorted(pd.to_datetime(audit.trade_date).unique(), reverse=True)
                audit_date = st.selectbox("选股信号日", audit_dates, format_func=lambda d: pd.Timestamp(d).strftime("%Y-%m-%d"), key="selection_date_"+first)
                st.dataframe(audit[pd.to_datetime(audit.trade_date).eq(audit_date)], hide_index=True, width="stretch")
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
