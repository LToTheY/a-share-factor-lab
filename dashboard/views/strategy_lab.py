"""Interactive, isolated strategy construction and backtesting page."""

from __future__ import annotations

import json
from dataclasses import asdict

import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard.backtest_details import conditions, ledger
from dashboard.components import drawdown_figure, equity_figure, number, pct
from dashboard.context import (
    DEFAULT_MARKET_FILE,
    page_intro,
    store,
)
from dashboard.views.experiment_history import EXPERIMENT_ROOT
from quant_lab.backtest.engine import BacktestConfig
from quant_lab.dashboard import ArtifactError
from quant_lab.dashboard.experiments import ExperimentStore, frame_fingerprint
from quant_lab.dashboard.strategy_data import StrategyDataStore
from quant_lab.strategy import StrategySpec, run_strategy
from quant_lab.strategy.sandbox import annual_returns

TEMPLATES = {
    "项目11因子等权": None,
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


def _factor_editor(summary: pd.DataFrame, template: str) -> pd.DataFrame:
    selected = TEMPLATES[template]
    frame = summary[["factor", "configured_direction"]].copy()
    frame["启用"] = True if selected is None else frame["factor"].isin(selected)
    frame["权重"] = 1.0
    frame["方向"] = "沿用项目方向"
    frame = frame.rename(
        columns={"factor": "因子", "configured_direction": "项目基础方向"}
    )
    return st.data_editor(
        frame[["启用", "因子", "项目基础方向", "权重", "方向"]],
        width="stretch",
        hide_index=True,
        disabled=["因子", "项目基础方向"],
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
        key=f"strategy_factor_editor_{template}",
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


def _run_experiment(
    factor_weights: dict[str, float],
    start_date,
    end_date,
    top_n: int,
    exit_rank: int,
    frequency: str,
    max_weight: float,
    minimum_coverage: float,
    initial_cash: float,
    commission_bps: float,
    stamp_duty_bps: float,
    historical_stamp_duty_bps: float,
    transfer_fee_bps: float,
    slippage_bps: float,
    minimum_commission: float,
):
    artifacts = store()
    allowed = artifacts.factor_names()
    data = StrategyDataStore(
        artifacts.root / "factor_scores.parquet", DEFAULT_MARKET_FILE
    )
    scores, market = data.load(
        list(factor_weights), start_date, end_date, allowed_factors=allowed
    )
    spec = StrategySpec(
        factor_weights=factor_weights,
        top_n=top_n,
        exit_rank=exit_rank,
        rebalance_frequency=frequency,
        max_weight=max_weight,
        minimum_factor_coverage=minimum_coverage,
    )
    config = BacktestConfig(
        initial_cash=initial_cash,
        commission_rate=commission_bps / 10_000,
        stamp_duty_rate=stamp_duty_bps / 10_000,
        historical_stamp_duty_rate=historical_stamp_duty_bps / 10_000,
        transfer_fee_rate=transfer_fee_bps / 10_000,
        slippage_bps=slippage_bps,
        minimum_commission=minimum_commission,
        lot_size=100,
    )
    source_summary = (
        artifacts.json("summary.json") if artifacts.exists("summary.json") else {}
    )
    provenance = {
        "requested_start": str(start_date),
        "requested_end": str(end_date),
        "data_end": str(data.date_bounds()[1].date()),
        "execution_timing": "收盘后信号，下一交易日开盘成交",
        "universe": "沿用因子分数中的历史 in_universe；基准使用行情 in_index",
        "research_settings": source_summary.get("research_settings", "旧报告未记录"),
        "factor_directions": artifacts.csv("factor_summary.csv")[
            ["factor", "configured_direction"]
        ].to_dict("records"),
        "market_fingerprint": frame_fingerprint(market),
        "score_fingerprint": frame_fingerprint(scores),
        "market_file": str(data.market_file),
        "score_file": str(data.score_file),
        "source_report_end": source_summary.get("end_date"),
        "benchmark": "历史时点成分股等权、每日再平衡、不计成本",
        "validation": "样本内实验，未经样本外确认",
    }
    return run_strategy(scores, market, spec, config), spec, config, provenance


def _display_result(
    result, spec: StrategySpec, config: BacktestConfig, provenance: dict
) -> None:
    saved_id = st.session_state.get("strategy_experiment_id")
    if saved_id:
        st.success(f"实验已保存到本机，可在“实验历史”查看与比较。编号：{saved_id[:8]}")
    else:
        st.warning("本次结果尚未写入历史，目前仅保存在当前会话。")
        if st.button("重试保存实验"):
            try:
                saved_id = ExperimentStore(EXPERIMENT_ROOT).save(
                    result,
                    spec,
                    config,
                    provenance,
                    st.session_state.get("experiment_saved_name", "未命名实验"),
                )
                st.session_state["strategy_experiment_id"] = saved_id
                st.session_state["experiment_created_at"] = ExperimentStore(
                    EXPERIMENT_ROOT
                ).metadata(saved_id)["created_at"]
                st.rerun()
            except (OSError, ValueError) as exc:
                st.error(f"保存失败：{exc}")
    conditions(
        {
            "strategy": asdict(spec),
            "backtest": asdict(config),
            "provenance": provenance,
            "actual_start": str(result.backtest.equity.trade_date.min().date()),
            "actual_end": str(result.backtest.equity.trade_date.max().date()),
            "created_at": st.session_state.get("experiment_created_at", "未记录"),
        }
    )
    metrics = result.metrics
    benchmark = result.benchmark_metrics
    diagnostics = result.diagnostics
    cols = st.columns(7)
    cols[0].metric("策略年化", pct(metrics["annual_return"]))
    cols[1].metric("基准年化", pct(benchmark["annual_return"]))
    cols[2].metric("年化超额", pct(diagnostics["annual_excess_return"]))
    cols[3].metric("Sharpe", number(metrics["sharpe"], 2))
    cols[4].metric("最大回撤", pct(metrics["max_drawdown"]))
    cols[5].metric("累计换手", number(metrics["turnover"], 1))
    cols[6].metric("交易次数", number(diagnostics["trade_count"], 0))

    if diagnostics["annual_excess_return"] < 0:
        st.warning(
            "该参数在所选区间没有跑赢诊断基准。不要因为结果不好而反复挑参数直到曲线变漂亮。"
        )

    st.plotly_chart(
        equity_figure(result.backtest.equity, result.benchmark, "策略实验净值"),
        width="stretch",
    )
    st.plotly_chart(drawdown_figure(result.backtest.equity), width="stretch")

    strategy_annual = annual_returns(result.backtest.equity).rename(
        columns={"return": "策略"}
    )
    benchmark_annual = annual_returns(result.benchmark).rename(
        columns={"return": "基准"}
    )
    annual = strategy_annual.merge(benchmark_annual, on="year", how="outer")
    annual_long = annual.melt(id_vars="year", var_name="组合", value_name="收益率")
    st.plotly_chart(
        px.bar(
            annual_long,
            x="year",
            y="收益率",
            color="组合",
            barmode="group",
            title="年度收益对比",
        ),
        width="stretch",
    )

    left, right = st.columns(2)
    with left:
        st.subheader("最新一期目标持仓")
        latest_date = result.targets["trade_date"].max()
        latest = result.targets.loc[result.targets["trade_date"] == latest_date]
        st.caption(f"信号日期：{latest_date:%Y-%m-%d}")
        st.dataframe(
            latest.sort_values("factor_rank")[
                ["symbol", "target_weight", "factor_processed", "factor_rank"]
            ],
            width="stretch",
            hide_index=True,
        )
    with right:
        st.subheader("实验诊断")
        st.markdown(
            f"""
            - 调仓次数：`{diagnostics["rebalance_count"]}`
            - 平均持股数：`{diagnostics["average_holdings"]:.1f}`
            - 估算交易费用与税：`{diagnostics["total_cost"]:,.2f}` 元
            - 初始资金：`{config.initial_cash:,.0f}` 元
            - 单股权重上限：`{spec.max_weight:.2%}`
            """
        )

    st.subheader("最近100笔成交")
    trades = result.backtest.trades.sort_values("trade_date", ascending=False)
    st.dataframe(trades.head(100), width="stretch", hide_index=True)
    ledger(
        result.backtest.equity,
        result.targets,
        result.backtest.trades,
        result.backtest.positions,
        result.backtest.execution_issues,
        key=f"experiment_day_{saved_id or 'unsaved'}",
    )

    export = {
        "strategy": asdict(spec),
        "backtest": asdict(config),
        "diagnostics": diagnostics,
        "metrics": metrics,
        "benchmark_metrics": benchmark,
        "provenance": provenance,
    }
    downloads = st.columns(3)
    downloads[0].download_button(
        "下载策略配置 JSON",
        data=json.dumps(export, ensure_ascii=False, indent=2),
        file_name="strategy_experiment.json",
        mime="application/json",
    )
    downloads[1].download_button(
        "下载净值 CSV",
        data=result.backtest.equity.to_csv(index=False),
        file_name="strategy_equity.csv",
        mime="text/csv",
    )
    downloads[2].download_button(
        "下载成交 CSV",
        data=result.backtest.trades.to_csv(index=False),
        file_name="strategy_trades.csv",
        mime="text/csv",
    )


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
    costs_seed = loaded.get("backtest", {})
    if loaded:
        st.info("已载入历史实验参数；更改后会生成新实验，原记录保留。")
        if st.button("清除载入参数"):
            st.session_state.pop("reload_strategy", None)
            st.rerun()
    preset = st.selectbox("小资金预设（元）", [3000, 5000, 10000], index=1)
    template = st.selectbox("起始模板", list(TEMPLATES))
    with st.form("strategy_builder"):
        name = st.text_input("实验名称", loaded.get("name", "价量策略实验"))
        hypothesis = st.text_area("研究假设（运行前填写）", loaded.get("hypothesis", "短期反转在扣除成本后仍具有稳定性。"))
        editor = _factor_editor(summary, template)
        if seed.get("factor_weights"):
            st.caption("载入的因子方向与权重如下，可修改 JSON 中的数值；正负号相对报告已配置的方向。")
            weight_text = st.text_area("载入因子权重 JSON", json.dumps(seed["factor_weights"], ensure_ascii=False))
        else:
            weight_text = None
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
            weights = json.loads(weight_text) if weight_text else _signed_weights(editor)
            if not set(weights).issubset(set(summary.factor)):
                raise ValueError("载入的因子不在当前报告中，请打开原研究报告或重新研究这些因子")
            spec = StrategySpec(weights, int(top_n), int(exit_rank), FREQUENCIES[frequency], maximum/100, coverage/100)
            config = BacktestConfig(initial_cash=cash, commission_rate=commission/10000, slippage_bps=slip, minimum_commission=minimum_fee, stamp_duty_rate=tax/10000, historical_stamp_duty_rate=old_tax/10000, transfer_fee_rate=transfer/10000)
            horizon = research_summary.get("research_settings", {}).get("forward_periods", 5)
            request = prepare_strategy_request(PROJECT_ROOT, {"name": name, "hypothesis": hypothesis, "report": str(artifacts.root.relative_to(PROJECT_ROOT)), "strategy": asdict(spec), "backtest": asdict(config), "universe": pool, "start": str(start), "end": str(end), "walk_forward": validate, "robustness": compare, "validation": {"train_years": int(train_years), "validation_years": int(validation_years), "test_years": int(test_years), "horizon": horizon, "embargo": horizon}})
            status = JobStore(PROJECT_ROOT).submit("strategy", request)
            st.success(f"实验任务已提交 {status['id'][:8]}。完成后自动保存，可在实验历史查看实际持仓、闲置现金、费用及逐日成交。")
        except (ValueError, OSError, KeyError, RuntimeError) as exc:
            st.error(str(exc))


if __name__ == "__main__":
    render()
