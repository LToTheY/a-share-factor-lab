"""Interactive, isolated strategy construction and backtesting page."""

from __future__ import annotations

import hashlib
from dataclasses import asdict

import pandas as pd
import streamlit as st

from dashboard.context import (
    page_intro,
    store,
)
from quant_lab.backtest.engine import BacktestConfig
from quant_lab.dashboard import ArtifactError
from quant_lab.dashboard.strategy_data import StrategyDataStore
from quant_lab.strategy import StrategySpec

TEMPLATES = {
    "报告因子等权": None,
    "模块等权（模块内等权）": None,
    "动量组合": {"momentum_20_5", "momentum_60_5", "momentum_120_20"},
    "反转与低波动": {
        "reversal_5",
        "reversal_20",
        "volatility_20",
        "volatility_60",
    },
}

FREQUENCIES = {
    "每日": "D",
    "每周（周五所在周）": "W-FRI",
    "每月": "M",
}


def factor_editor_frame(summary: pd.DataFrame, template: str, seed_weights=None) -> pd.DataFrame:
    from quant_lab.dashboard.catalog import category_for
    from quant_lab.research.service import BUILTIN_INPUTS

    selected = TEMPLATES[template]
    frame = summary[["factor", "configured_direction"]].copy()
    frame["参考模块"] = frame.factor.map(lambda name: category_for(name) if name in BUILTIN_INPUTS else "自定义/其他")
    frame["启用"] = True if selected is None else frame["factor"].isin(selected)
    frame["权重"] = 1.0
    frame["方向"] = "沿用项目方向"
    if template == "模块等权（模块内等权）":
        frame["权重"] = 1. / frame.groupby("参考模块")["factor"].transform("count")
    if seed_weights:
        frame["启用"] = frame.factor.isin(seed_weights)
        frame["权重"] = frame.factor.map(seed_weights).fillna(0.).astype(float).abs()
        frame.loc[frame.factor.map(seed_weights).fillna(0.).astype(float).lt(0), "方向"] = "反向"
    frame = frame.rename(
        columns={"factor": "因子", "configured_direction": "项目基础方向"}
    )
    return frame[["启用", "因子", "参考模块", "项目基础方向", "权重", "方向"]]


def _factor_editor(summary: pd.DataFrame, template: str, seed_weights=None) -> pd.DataFrame:
    frame = factor_editor_frame(summary, template, seed_weights)
    fingerprint = hashlib.sha256(frame.to_json().encode()).hexdigest()[:12]
    return st.data_editor(
        frame,
        width="stretch",
        hide_index=True,
        disabled=["因子", "参考模块", "项目基础方向"],
        column_config={
            "启用": st.column_config.CheckboxColumn("启用"),
            "权重": st.column_config.NumberColumn(
                "相对权重", min_value=0.0, max_value=10.0, step=0.25
            ),
            "方向": st.column_config.SelectboxColumn(
                "方向",
                options=["沿用项目方向", "反向"],
                required=True,
            ),
        },
        key=f"strategy_factor_editor_{template}_{fingerprint}",
    )


def _signed_weights(editor: pd.DataFrame) -> dict[str, float]:
    enabled = editor.loc[editor["启用"].fillna(False)].copy()
    weights: dict[str, float] = {}
    for row in enabled.itertuples(index=False):
        weight = float(row.权重)
        if row.方向 == "反向":
            weight *= -1.0
        weights[str(row.因子)] = weight
    return weights






def render() -> None:
    from dashboard.context import PROJECT_ROOT
    from dashboard.research_runner import POOL_LABELS, jobs_panel, version_notice
    from quant_lab.research.jobs import JobStore
    from quant_lab.research.strategy_service import prepare_strategy_request

    page_intro("策略实验室", "当前实验的回测、样本外检验和对照一起保存；真实模拟账户不受实验资金设置影响。")
    artifacts = store()
    jobs_panel("strategy")
    try:
        summary = artifacts.csv("factor_summary.csv")
        source = artifacts.json("dataset_provenance.json")
        version_notice(artifacts)
        minimum_date, maximum_date = StrategyDataStore(artifacts.root / "factor_scores.parquet", PROJECT_ROOT / source.get("market_file", "data/processed/market_daily.parquet")).date_bounds()
        research_summary = artifacts.json("summary.json")
        minimum_date = max(minimum_date, pd.Timestamp(research_summary.get("research_start_date", minimum_date)))
    except (ArtifactError, ValueError, KeyError) as exc:
        st.info(f"请先在运行因子研究页面生成并打开研究结果：{exc}")
        return
    loaded = st.session_state.get("reload_strategy", {})
    seed = loaded.get("strategy", {})
    missing_loaded_factors = set(seed.get("factor_weights", {})) - set(summary.factor)
    costs_seed = loaded.get("backtest", {})
    if loaded:
        st.info("已载入历史实验参数；更改后会生成新实验，原记录保留。")
        if missing_loaded_factors:
            st.warning("当前报告缺少载入的因子：" + ", ".join(sorted(missing_loaded_factors)) + "。请打开原报告或清除载入参数。")
        if st.button("清除载入参数"):
            st.session_state.pop("reload_strategy", None)
            st.rerun()
    preset = st.selectbox("小资金预设（元）", [3000, 5000, 10000], index=1)
    template = st.selectbox("起始模板", list(TEMPLATES))
    if template == "模块等权（模块内等权）":
        st.caption("先让各参考模块的绝对权重之和相同，再在模块内等分。例如3个动量因子各1/3，2个反转因子各1/2。它不是风险等权，也不证明模块相互独立；手动修改后以表格为准。")
    with st.form("strategy_builder"):
        name = st.text_input("实验名称", loaded.get("name", "价量策略实验"))
        hypothesis = st.text_area("研究假设（运行前填写）", loaded.get("hypothesis", "检验所选因子在预设方向、资金和费用约束下是否仍有排序信息；查看样本外与失败对照。"))
        editor = _factor_editor(summary, template, seed.get("factor_weights"))
        if seed.get("factor_weights"):
            st.caption("历史因子权重和方向已填入上表，修改上表就会改变本次计算。载入权重优先于起始模板；要应用新模板，请先清除载入参数。")
        pool = st.selectbox("实验股票池", list(POOL_LABELS), index=list(POOL_LABELS).index(loaded.get("universe", source.get("universe", "000905.SH"))), format_func=POOL_LABELS.get)
        dates = st.columns(2)
        start = dates[0].date_input("开始日期", pd.Timestamp(loaded.get("start", minimum_date)).date(), min_value=minimum_date.date(), max_value=maximum_date.date())
        end = dates[1].date_input("结束日期", pd.Timestamp(loaded.get("end", maximum_date)).date(), min_value=minimum_date.date(), max_value=maximum_date.date())
        rules = st.columns(4)
        top_n = rules[0].number_input("持股数量 Top N", 1, 100, int(seed.get("top_n", 5)))
        exit_rank = rules[1].number_input("退出排名", 1, 200, int(seed.get("exit_rank", 30)))
        frequency = rules[2].selectbox("调仓频率", list(FREQUENCIES), index=list(FREQUENCIES.values()).index(seed.get("rebalance_frequency", "W-FRI")))
        maximum = rules[3].number_input("单股上限（%）", 1.0, 100.0, float(seed.get("max_weight", .2))*100)
        coverage = st.number_input("最低因子完整度（%）", 1.0, 100.0, float(seed.get("minimum_factor_coverage", .8))*100)
        modes = {"affordable": "按预算筛选可负担候选", "rank": "按排名选股（允许买不起而空仓）"}
        selection = st.selectbox("选股与资金约束", list(modes), index=list(modes).index(seed.get("selection_mode", "affordable")), format_func=modes.get)
        buffer = st.number_input("现金预留（%）", 0.0, 50.0, float(seed.get("cash_buffer", .02))*100)
        boards = st.columns(2)
        allow_star = boards[0].checkbox("允许科创板候选（需自行确认权限）", value=seed.get("allow_star", False))
        allow_chinext = boards[1].checkbox("允许创业板候选（需自行确认权限）", value=seed.get("allow_chinext", False))
        st.caption("可负担筛选使用信号日收盘价、实际持仓和费用估算；跳空、停牌及涨跌停仍可使下一日无法成交。固定单股预算不会为凑满仓而放宽。")
        with st.expander("资金与费用", expanded=True):
            cash = st.number_input("初始资金（元）", 100.0, 100000000.0, float(costs_seed.get("initial_cash", preset)), step=100.0)
            commission = st.number_input("佣金（基点）", 0.0, 100.0, float(costs_seed.get("commission_rate", .0003))*10000)
            slip = st.number_input("单边滑点（基点）", 0.0, 100.0, float(costs_seed.get("slippage_bps", 5)))
            minimum_fee = st.number_input("最低佣金（元）", 0.0, 100.0, float(costs_seed.get("minimum_commission", 5)))
            tax = st.number_input("当前卖出印花税（基点）", 0.0, 100.0, float(costs_seed.get("stamp_duty_rate", .0005))*10000)
            old_tax = st.number_input("历史卖出印花税（基点）", 0.0, 100.0, float(costs_seed.get("historical_stamp_duty_rate", .001))*10000)
            transfer = st.number_input("过户费（基点）", 0.0, 100.0, float(costs_seed.get("transfer_fee_rate", .00001))*10000)
        validate = st.checkbox("运行本策略的 Walk-forward", value=loaded.get("walk_forward", True))
        st.caption("默认5年训练、1年验证、1年测试；隔离期不短于预测期。权重和参数保持固定，各折独立从初始资金开始。")
        wf_seed = loaded.get("validation", {})
        windows = st.columns(3)
        train_years = windows[0].number_input("训练年数", 1, 15, int(wf_seed.get("train_years", 5)))
        validation_years = windows[1].number_input("验证年数", 1, 5, int(wf_seed.get("validation_years", 1)))
        test_years = windows[2].number_input("测试年数", 1, 5, int(wf_seed.get("test_years", 1)))
        compare = st.checkbox("运行有限单因素对照（5/10/20只、周/月、基准/双倍成本）", value=loaded.get("robustness", True))
        submitted = st.form_submit_button("运行策略回测", type="primary")
    if submitted:
        try:
            if missing_loaded_factors:
                raise ValueError("载入的因子不在当前报告中，请打开原报告或清除载入参数；不会静默丢弃旧因子")
            weights = _signed_weights(editor)
            if not isinstance(weights, dict):
                raise TypeError("因子权重 JSON 必须是名称到数值的对象，例如 {\"reversal_5\": 1.0}")
            if not set(weights).issubset(set(summary.factor)):
                raise ValueError("载入的因子不在当前报告中，请打开原研究报告或重新研究这些因子")
            spec = StrategySpec(weights, int(top_n), int(exit_rank), FREQUENCIES[frequency], maximum/100, coverage/100,
                                selection_mode=selection, cash_buffer=buffer/100,
                                allow_star=allow_star, allow_chinext=allow_chinext)
            config = BacktestConfig(initial_cash=cash, commission_rate=commission/10000, slippage_bps=slip, minimum_commission=minimum_fee, stamp_duty_rate=tax/10000, historical_stamp_duty_rate=old_tax/10000, transfer_fee_rate=transfer/10000)
            horizon = research_summary.get("research_settings", {}).get("forward_periods", 5)
            request = prepare_strategy_request(PROJECT_ROOT, {"name": name, "hypothesis": hypothesis, "report": str(artifacts.root.relative_to(PROJECT_ROOT)), "strategy": asdict(spec), "backtest": asdict(config), "universe": pool, "start": str(start), "end": str(end), "walk_forward": validate, "robustness": compare, "validation": {"train_years": int(train_years), "validation_years": int(validation_years), "test_years": int(test_years), "horizon": horizon, "embargo": horizon}})
            status = JobStore(PROJECT_ROOT).submit("strategy", request)
            st.success(f"实验任务已提交 {status['id'][:8]}。完成后自动保存，可在实验历史查看实际持仓、闲置现金、费用及逐日成交。")
        except (ValueError, OSError, KeyError, RuntimeError, TypeError) as exc:
            st.error(str(exc))


if __name__ == "__main__":
    render()
