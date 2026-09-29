"""One-click local studies and persistent job monitoring."""

import json
from copy import deepcopy
from datetime import date

import streamlit as st
import yaml

from dashboard.context import DEFAULT_REPORT_DIR, PROJECT_ROOT
from quant_lab.factors.custom_loader import custom_factor_metadata
from quant_lab.research.jobs import JobStore
from quant_lab.research.service import (
    BUILTIN_INPUTS,
    POOLS,
    create_demo,
    factor_version,
)

POOL_LABELS = {"000300.SH": "历史沪深300", "000905.SH": "历史中证500", "CSI800": "历史沪深300与中证500并集"}


def jobs_panel(kind=None):
    storage = JobStore(PROJECT_ROOT)
    if st.button("刷新任务状态", key=f"refresh_jobs_{kind}"):
        st.rerun()
    records = [s for s in storage.list() if kind is None or s["kind"] == kind]
    if not records:
        st.info("尚未提交任务。完成配置后即可运行，刷新页面不会丢失进度。")
    for status in records[:5]:
        with st.expander(f"{status['kind']} · {status['state']} · {status['created_at']} · {status['id'][:8]}", expanded=status["state"] in {"queued", "running", "cancelling"}):
            st.write(status["message"])
            if status["state"] in {"queued", "running", "cancelling"}:
                if st.button("取消任务", key="cancel_" + status["id"]):
                    storage.cancel(status["id"])
                    st.info("已请求取消；在当前计算单元结束后停止，不发布候选结果。")
            elif status["state"] == "succeeded" and status["kind"] == "research":
                if st.button("打开研究结果", key="open_" + status["id"]):
                    st.session_state["report_dir"] = str(PROJECT_ROOT / status["result"])
                    st.success("已打开。下一步进入因子诊断或策略实验室。")
            elif status["state"] == "succeeded" and status["kind"] == "strategy":
                experiment = json.loads((PROJECT_ROOT / status["result"] / "experiment.json").read_text(encoding="utf-8"))
                st.success(f"已保存实验 {experiment['id'][:8]}，可在实验历史重开、回放、比较。")
                validation = experiment["validation"]
                st.write(validation.get("message") or validation.get("method"))
                if validation.get("folds"):
                    st.dataframe(validation["folds"], hide_index=True)
                if experiment.get("robustness"):
                    st.dataframe(experiment["robustness"], hide_index=True)
            st.code(storage.log(status["id"]), language="text")


def report_selector():
    candidates = [p.parent for p in (PROJECT_ROOT / "reports/generated").glob("**/result_manifest.json")
                  if not any(part.startswith(".pending") for part in p.parts)]
    if (DEFAULT_REPORT_DIR / "summary.json").exists() and DEFAULT_REPORT_DIR not in candidates:
        candidates.append(DEFAULT_REPORT_DIR)
    if candidates:
        selected = st.selectbox("已保存的研究报告", sorted(candidates), format_func=lambda p: str(p.relative_to(PROJECT_ROOT)))
        if st.button("切换当前研究报告"):
            st.session_state["report_dir"] = str(selected)
            st.rerun()
    st.caption(f"当前报告：{st.session_state.get('report_dir', str(DEFAULT_REPORT_DIR))}")


def version_notice(artifacts):
    if not artifacts.exists("dataset_provenance.json"):
        st.warning("历史报告缺少版本记录，请重新运行研究后再配置策略。")
        return
    source = artifacts.json("dataset_provenance.json")
    reasons = []
    if source.get("factor_version") != factor_version(PROJECT_ROOT):
        reasons.append("因子代码已变化或未记录")
    market_path = PROJECT_ROOT / source.get("market_file", "data/processed/market_daily.parquet")
    manifest_path = market_path.with_suffix(".manifest.json")
    if manifest_path.exists():
        latest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if latest.get("dataset_id") != source.get("dataset_id"):
            reasons.append("行情已更新")
    if reasons:
        st.warning("历史版本：" + "；".join(reasons) + "。旧报告仍可阅读，新实验需要重建研究。")
    if source.get("provider") == "synthetic":
        st.warning("合成数据演示：所有股票和收益均用于验证流程，不是真实投资表现。")


def render():
    st.title("运行因子研究")
    st.caption("选择股票池和因子 → 后台计算 → 打开结果 → 因子诊断 → 策略实验")
    demo = st.toggle("合成数据演示（无需国泰安账号）", value=not (PROJECT_ROOT / "data/processed/market_daily.parquet").exists())
    base = yaml.safe_load((PROJECT_ROOT / "configs/research.yaml").read_text(encoding="utf-8"))
    metadata = custom_factor_metadata()
    defaults = {item["name"]: item.get("direction", 1) for item in base["factors"]["definitions"]}
    defaults.update({name: item["direction"] for name, item in metadata.items()})
    from quant_lab.data.freshness import shanghai_now
    latest_date = shanghai_now().date()
    manifest = PROJECT_ROOT / "data/processed/market_daily.manifest.json"
    if manifest.exists():
        latest_date = date.fromisoformat(json.loads(manifest.read_text(encoding="utf-8"))["end_date"])
    with st.form("research_request"):
        selected = st.multiselect("参加研究的内置 / 自定义因子", list(dict.fromkeys([*BUILTIN_INPUTS, *metadata])), default=["reversal_5"])
        pool = st.selectbox("股票池", list(POOLS), index=2 if demo else 1, format_func=POOL_LABELS.get)
        start = st.date_input("研究开始日期", date(2016, 1, 1))
        end = st.date_input("研究结束日期", date(2024, 12, 18) if demo else latest_date)
        st.caption("系统自动读取开始日前所需预热历史。换股票池会重新做截面标准化。因子方向可在策略实验中调整。")
        submitted = st.form_submit_button("启动因子研究", type="primary")
    if submitted:
        try:
            if not selected:
                raise ValueError("至少选择一个因子")
            config = create_demo(PROJECT_ROOT) if demo else deepcopy(base)
            config["data"].update(research_start_date=str(start), research_end_date=str(end))
            config["universe"]["preferred_index"] = pool
            config["factors"]["definitions"] = [{"name": name, "direction": defaults.get(name, 1)} for name in selected]
            config["factors"]["minimum_valid_factors"] = len(selected)
            status = JobStore(PROJECT_ROOT).submit_research(config)
            st.success(f"任务已提交：{status['id'][:8]}。可以离开本页，稍后回来查看。")
        except (ValueError, OSError, RuntimeError) as exc:
            st.error(str(exc))
    jobs_panel("research")
    st.divider()
    report_selector()
    from dashboard.custom_factors import render_python_factors as custom_render

    custom_render()
