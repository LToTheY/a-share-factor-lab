"""Refresh, verify, then publish a dated human-reviewable rebalance check."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

import numpy as np
import pandas as pd

from quant_lab.data.baostock_client import BaoStockDownloader
from quant_lab.data.csmar_sample import _write_json
from quant_lab.data.current_market import refresh_current_market
from quant_lab.data.freshness import session_target, shanghai_now
from quant_lab.data.process_lock import data_lock
from quant_lab.data.storage import write_table
from quant_lab.data.storage_budget import StorageBudget, write_budgeted_frame
from quant_lab.evaluation.preprocess import preprocess_factor, zscore
from quant_lab.factors.library import compute_factor
from quant_lab.portfolio.paper import ORDER_COLUMNS, build_next_day_orders
from quant_lab.research.settings import load_research_settings
from quant_lab.utils.config import load_config


def score_current_market(market: pd.DataFrame, settings, state: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Score only the latest cross-section; this is not a historical backtest."""
    if settings.neutralize_size or settings.neutralize_industry:
        raise ValueError("实时检查暂未接入已核验的历史市值/行业中性化数据")
    date = market["trade_date"].max()
    latest = market.loc[market["trade_date"].eq(date)].copy()
    eligible = (latest["in_index"] & ~latest["is_st"] & ~latest["is_suspended"]
                & (latest["listing_age_sessions"] + 1).ge(settings.min_listed_days)
                & latest["amount"].ge(settings.min_amount))
    eligible &= latest[["is_st_known", "is_suspended_known", "limit_status_known"]].all(axis=1)
    if "amount_outside_price_range" in latest:
        eligible &= ~latest["amount_outside_price_range"]
    latest["in_universe"] = eligible
    if not eligible.any():
        raise ValueError("最新股票池没有通过筛选的股票")
    score_names, coverage = [], {}
    for definition in settings.factors:
        factor = compute_factor(market, definition.name)
        factor = factor.loc[factor["trade_date"].eq(date)].copy()
        factor = latest[["trade_date", "symbol", "in_universe"]].merge(factor, on=["trade_date", "symbol"], validate="one_to_one")
        factor.loc[~factor["in_universe"], "factor"] = np.nan
        fraction = float(factor.loc[factor["in_universe"], "factor"].notna().mean())
        coverage[definition.name] = fraction
        processed = preprocess_factor(factor, n_mad=settings.winsor_n_mad, neutralize_size=False, neutralize_industry=False)
        values = processed.set_index("symbol")["factor_processed"] * definition.direction
        latest[definition.name] = latest["symbol"].map(values) if fraction >= settings.minimum_factor_coverage else np.nan
        score_names.append(definition.name)
    latest["valid_factor_count"] = latest[score_names].notna().sum(axis=1)
    composite = latest[score_names].mean(axis=1).where(latest["valid_factor_count"].ge(settings.minimum_valid_factors) & eligible)
    latest["factor_processed"] = zscore(composite)
    latest["factor_rank"] = latest["factor_processed"].rank(method="first", ascending=False)
    usable = latest.loc[eligible & latest["factor_processed"].notna()].sort_values(["factor_rank", "symbol"])
    if len(usable) < settings.top_n or len(usable) / int(eligible.sum()) < settings.minimum_factor_coverage:
        raise ValueError("最新因子覆盖不足，禁止以不完整排名生成调仓建议")
    held = {s for s, quantity in state.get("positions", {}).items() if int(quantity) > 0}
    kept = usable.loc[usable["symbol"].isin(held) & usable["factor_rank"].le(settings.exit_rank), "symbol"].tolist()[:settings.top_n]
    for symbol in usable["symbol"]:
        if len(kept) >= settings.top_n:
            break
        if symbol not in kept:
            kept.append(symbol)
    targets = usable.loc[usable["symbol"].isin(kept), ["trade_date", "symbol", "factor_processed", "factor_rank"]].copy()
    targets["target_weight"] = min(1.0 / len(kept), settings.max_weight)
    return usable, targets, coverage


def run_current_check(root: Path, config_path: Path, *, now=None, client_factory=BaoStockDownloader, progress_hook=None) -> dict:
    config = load_config(config_path)
    settings = load_research_settings(config_path)
    current = config.get("current_check", {})
    budget = StorageBudget(root / "data")
    status_path = root / "data/state/current_check.json"
    report_root = root / "reports/generated/current_check"
    clock = lambda: pd.Timestamp(now) if now is not None else shanghai_now()
    with data_lock(root / "data/state/market_download.lock"):
        run_id = clock().strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8]
        status = {"status": "updating", "run_id": run_id, "pid": os.getpid(),
                  "started_at": clock().isoformat(), "message": "正在更新数据；旧建议不可用于本次判断",
                  "audit_passed": False, "orders_actionable": False,
                  "data_ready_hour": int(current.get("data_ready_hour", 18)),
                  "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest()}
        _write_json(status_path, status, budget)
        report_root.mkdir(parents=True, exist_ok=True)
        # Old proposals cannot remain the apparent output after a failed run.
        blocked = pd.DataFrame([{"status": "DATA_NOT_READY", "reason": "正在更新数据，请等待本次检查完成"}], columns=ORDER_COLUMNS)
        write_table(blocked, report_root / "next_day_orders.csv")
        client = client_factory()
        try:
            for attempt in range(3):
                try:
                    client.reconnect()
                    break
                except Exception:  # noqa: BLE001 - vendor network errors vary
                    if attempt == 2:
                        raise RuntimeError("BaoStock连接失败，3次重试后停止；没有生成新建议") from None
                    time.sleep(2)
            state_path = root / settings.paper_state_file
            state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {
                "cash": float(settings.backtest["initial_cash"]), "positions": {}, "as_of_date": None,
            }
            state_hash = hashlib.sha256(state_path.read_bytes()).hexdigest() if state_path.exists() else None
            holdings = {s for s, q in state.get("positions", {}).items() if int(q) != 0}
            def progress(message, **_):
                if progress_hook:
                    progress_hook(message)
                status["message"] = message
                status["heartbeat_at"] = clock().isoformat()
                _write_json(status_path, status, budget)
                print(message, flush=True)
            market, gate = refresh_current_market(
                client, root, budget, now=clock(), holdings=holdings,
                preferred_index=config["universe"].get("preferred_index", "000905.SH"),
                ready_hour=int(current.get("data_ready_hour", 18)),
                lookback_sessions=int(current.get("lookback_sessions", 260)), progress=progress,
            )
            status["data_check"] = gate
            if not gate["passed"]:
                raise ValueError("；".join(gate["errors"]))
            status["status"] = "calculating"
            status["message"] = "数据检查通过，正在计算最新因子和纸面调仓建议"
            _write_json(status_path, status, budget)
            signals, targets, coverage = score_current_market(market, settings, state)
            orders = build_next_day_orders(
                signals, targets, state, gate["next_trade_date"], settings.rebalance_frequency,
                lot_size=int(settings.backtest["lot_size"]),
                reference_prices=market.loc[market["trade_date"].eq(pd.Timestamp(gate["data_through"])), ["symbol", "close"]],
            )
            if not set(orders["status"]).issubset({"NO_TRADE", "REVIEW_REQUIRED"}):
                raise ValueError("纸面账户或交易日信息不足，未发布建议")
            calendar = pd.read_parquet(root / "data/raw/baostock_supplement/reference/current_calendar.parquet")
            expected, upcoming = session_target(calendar, clock(), int(current.get("data_ready_hour", 18)))
            if str(expected.date()) != gate["data_through"]:
                raise ValueError("运行期间进入新的行情更新时间，请重新更新后计算")
            current_state_hash = hashlib.sha256(state_path.read_bytes()).hexdigest() if state_path.exists() else None
            if state_hash != current_state_hash or status["config_sha256"] != hashlib.sha256(config_path.read_bytes()).hexdigest():
                raise ValueError("运行期间配置或模拟持仓发生变化，请重新检查")
            if progress_hook:
                progress_hook("发布本次数据检查结果")
            output = report_root / "runs" / run_id
            output.mkdir(parents=True, exist_ok=True)
            write_budgeted_frame(market, root / "data/processed/current_market.parquet", budget)
            for name, frame in [("latest_signal.csv", signals), ("target_weights.csv", targets), ("next_day_orders.csv", orders)]:
                write_table(frame, output / name)
            write_table(orders, report_root / "next_day_orders.csv")
            status.update({"status": "ready", "message": "最新数据和因子检查通过，结果仅供人工复核",
                           "audit_passed": True, "orders_actionable": False,
                           "data_through": gate["data_through"], "next_trade_date": str(upcoming.date()),
                           "order_status": str(orders.iloc[0]["status"]), "factor_coverage": coverage,
                           "report_dir": str(output.relative_to(root)), "completed_at": clock().isoformat(),
                           "account_type": "paper", "paper_cash": float(state["cash"]),
                           "paper_state_file": settings.paper_state_file,
                           "paper_state_sha256": state_hash,
                           "price_source_priority": ["csmar", "baostock"],
                           "adjustment_method": "exchange_preclose_chain", "mode": "latest_check_only"})
            _write_json(status_path, status, budget)
            return status
        except Exception as exc:  # noqa: BLE001 - persist a fail-closed result on every failure
            status.update({"status": "blocked", "message": str(exc), "error_type": type(exc).__name__,
                           "audit_passed": False, "orders_actionable": False, "finished_at": clock().isoformat()})
            _write_json(status_path, status, budget)
            blocked.loc[0, "reason"] = status["message"]
            write_table(blocked, report_root / "next_day_orders.csv")
            return status
        finally:
            client.__exit__()
