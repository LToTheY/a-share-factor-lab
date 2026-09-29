"""Saved-strategy signals for a user-entered account, without broker access."""

import hashlib
import json

import pandas as pd
import streamlit as st

from dashboard.context import PROJECT_ROOT, page_intro
from dashboard.order_display import order_preview
from dashboard.research_runner import jobs_panel
from quant_lab.dashboard.experiments import ExperimentStore
from quant_lab.data.freshness import current_result_status
from quant_lab.research.jobs import JobStore
from quant_lab.research.manual_review import create_profile, load_profile, profile_path
from quant_lab.research.service import code_version, local_path


def positions_from_table(frame):
    positions = {}
    for row in frame.to_dict("records"):
        symbol, shares = row.get("股票代码"), row.get("持仓股数")
        if pd.isna(symbol) and pd.isna(shares):
            continue
        if pd.isna(symbol) or not str(symbol).strip() or pd.isna(shares):
            raise ValueError("每一行持仓都需要股票代码和股数；多余空行请删除")
        symbol = str(symbol).strip().upper()
        if symbol in positions:
            raise ValueError(f"股票代码重复：{symbol}；请合并为完整持仓股数")
        positions[symbol] = shares
    return positions


def render():
    page_intro("我的策略每日复核", "从已保存的真实数据实验生成最新信号，用你填写的账户快照估算；由你手动决定和下单。")
    st.caption("这里没有券商连接。每次交易后，使用最新持仓重新建立方案；旧快照不会自动变成实际成交记录。")
    records, errors = ExperimentStore(PROJECT_ROOT / "data/state/strategy_experiments").list()
    for error in errors:
        st.warning(error)
    real = {r["id"]: r for r in records if r["provenance"].get("provider") == "csmar_baostock"}
    with st.expander("从实验建立复核方案", expanded=True):
        if not real:
            st.info("请先使用真实数据完成并保存一次策略实验。合成演示仅用于学习操作。")
        else:
            entry_mode = st.radio("持仓输入方式", ["逐行填写", "JSON"], horizontal=True)
            with st.form("manual_review_profile"):
                chosen = st.selectbox("选择真实数据实验", list(real), format_func=lambda key: f"{real[key]['name']} · {key[:8]}")
                cash = st.number_input("当前可用现金（元）", min_value=0., value=5000., step=100.)
                if entry_mode == "JSON":
                    positions = st.text_area("当前持仓 JSON（代码: 股数）", "{}", help='例如 {"000001.SZ": 100}；空仓填写 {}。不要填写密码或券商账号。')
                else:
                    positions = st.data_editor(pd.DataFrame({"股票代码": pd.Series(dtype=str), "持仓股数": pd.Series(dtype=float)}),
                                               num_rows="dynamic", hide_index=True, width="stretch", key="manual_positions_table",
                                               column_config={"股票代码": st.column_config.TextColumn(help="例如 000001.SZ 或 600000.SH", required=True),
                                                              "持仓股数": st.column_config.NumberColumn(min_value=0, step=1, format="%d", required=True)})
                    st.caption("空仓时保持表格为空；有持仓时逐行列全，以股为单位，不是手数。已有持仓应填写券商显示的实际股数。")
                snapshot_note = st.text_input("账户快照说明（可选）", placeholder="例如：模拟观察，或已核对至某日收盘；不要填写券商账号")
                stop_amount = st.number_input("自行设定的最大可承受亏损（元，0表示尚未设定）", min_value=0., value=0., step=50.)
                submit = st.form_submit_button("保存本机方案并更新数据", type="primary")
            if submit:
                try:
                    state = {"cash": cash, "positions": json.loads(positions) if entry_mode == "JSON" else positions_from_table(positions),
                             "risk_limits": {"maximum_loss_amount": stop_amount},
                             "snapshot_note": snapshot_note.strip() or "用户填写；未与券商核对"}
                    profile = create_profile(PROJECT_ROOT, chosen, state)
                    st.session_state["manual_profile_id"] = profile["id"]
                    JobStore(PROJECT_ROOT).submit("manual_review", {"profile_id": profile["id"]})
                    st.success("方案已保存在本机，正在更新行情并核对本策略。")
                except (ValueError, OSError, KeyError, RuntimeError, TypeError) as exc:
                    st.error(str(exc))
    jobs_panel("manual_review")
    profiles = {}
    for path in (PROJECT_ROOT / "data/state/manual_review").glob("*/profile.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            profiles[item["id"]] = item
        except (ValueError, OSError, KeyError):
            st.warning(f"有一份方案暂不可读：{path.parent.name}")
    if not profiles:
        return
    ids = sorted(profiles, key=lambda key: profiles[key]["created_at"], reverse=True)
    active = st.session_state.get("manual_profile_id", ids[0])
    chosen = st.selectbox("查看本机复核方案", ids, index=ids.index(active) if active in ids else 0,
                          format_func=lambda key: f"{profiles[key]['name']} · {profiles[key]['created_at']} · {key[:8]}")
    try:
        profile = load_profile(PROJECT_ROOT, chosen)
        if st.button("使用此账户快照重新更新", key="rerun_manual"):
            JobStore(PROJECT_ROOT).submit("manual_review", {"profile_id": chosen})
            st.info("已提交；如果持仓或现金已经变化，请先建立新方案。")
        validation = profile.get("historical_validation", {})
        if not isinstance(validation, dict) or validation.get("status") != "ready":
            st.warning("来源实验尚未完成足够的样本外验证。当前输出可用于观察或模拟记录，不能视为策略已经通过验证。")
        historical = profile.get("research_metrics", {})
        diagnostics = profile.get("research_diagnostics", {})
        if historical:
            st.caption(f"来源实验初始资金 {profile.get('research_initial_cash', 0):,.2f} 元；历史总收益 {historical.get('total_return', float('nan')):.1%}；最大回撤 {historical.get('max_drawdown', float('nan')):.1%}。当前账户资金与持仓不同，不能直接套用历史收益。")
        if historical.get("total_return", 0) < 0:
            st.warning("来源实验在历史区间亏损；生成新信号只证明流程可运行，不代表该策略已适合实盘。")
        if diagnostics.get("stale_valuation_events", 0) or diagnostics.get("corporate_action_events", 0):
            st.warning("来源回测包含估值或分红送转账本近似，请先在实验历史查看异常和局限。")
        account = json.loads(local_path(PROJECT_ROOT, profile["account_file"], area="data/state/manual_review").read_text(encoding="utf-8"))
        st.write(f"填写的可用现金：{account['cash']:,.2f} 元；持仓：", account["positions"])
        st.caption(f"账户说明：{account.get('snapshot_note', '未记录；请自行核对是否仍与实际账户一致')}")
        limit = account.get("risk_limits", {}).get("maximum_loss_amount", 0)
        st.caption(f"自行设定最大可承受亏损：{limit:,.2f} 元。该值用于人工复核，不会自动止损。" if limit else "尚未记录最大可承受亏损；实际投入前先确定停止条件。")
        status_path = profile_path(PROJECT_ROOT, chosen) / "status.json"
        if not status_path.is_file():
            st.info("方案已保存，尚无完成的数据检查。")
            return
        status = json.loads(status_path.read_text(encoding="utf-8"))
        calendar_path = PROJECT_ROOT / "data/raw/baostock_supplement/reference/current_calendar.parquet"
        calendar = pd.read_parquet(calendar_path) if calendar_path.exists() else pd.DataFrame()
        valid, reason = current_result_status(status, calendar, ready_hour=status.get("data_ready_hour", 18))
        for field, key in [("config_file", "config_sha256"), ("account_file", "paper_state_sha256")]:
            if hashlib.sha256(local_path(PROJECT_ROOT, profile[field], area="data/state/manual_review").read_bytes()).hexdigest() != status.get(key):
                valid, reason = False, "方案或账户快照已变化，请重新更新检查"
        if status.get("code_version") != code_version(PROJECT_ROOT):
            valid, reason = False, "计算代码已变化，请重新更新检查"
        if not valid:
            st.warning(reason)
            return
        st.success(reason)
        st.write(f"数据截止：{status['data_through']}；计划交易日：{status['next_trade_date']}")
        output = local_path(PROJECT_ROOT, status["report_dir"], area=f"reports/generated/manual_review/{chosen}")
        orders = pd.read_csv(output / "next_day_orders.csv")
        st.subheader("人工复核清单")
        st.dataframe(order_preview(orders), hide_index=True, width="stretch")
        st.caption("金额按上次收盘及费用假设估算。次日价格和交易状态尚未知；卖单未成交时，需要重新计算买入预算。")
        st.download_button("导出本次复核清单", orders.to_csv(index=False).encode("utf-8-sig"), file_name=f"manual_review_{status['data_through']}.csv", mime="text/csv")
        signals = pd.read_csv(output / "latest_signal.csv")
        with st.expander("查看最新因子排名与候选筛选"):
            st.dataframe(signals.head(100), hide_index=True, width="stretch")
            if (output / "selection_audit.csv").exists():
                st.dataframe(pd.read_csv(output / "selection_audit.csv"), hide_index=True, width="stretch")
    except (ValueError, OSError, KeyError, RuntimeError, TypeError) as exc:
        st.error(f"复核结果暂不可用：{exc}")
