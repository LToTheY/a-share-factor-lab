"""Opt-in real-data engineering acceptance with a clearly hypothetical account.

Runs a predeclared strategy for pipeline verification, never selects a winner,
connects to a broker, changes the default paper account, or places an order.
Requires a fresh factor report and BaoStock connectivity; results remain local.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from verify_dashboard_flow import page, wait_new_job, widget

from quant_lab.backtest.engine import BacktestConfig
from quant_lab.research.jobs import JobStore
from quant_lab.research.manual_review import profile_path
from quant_lab.research.service import code_version, file_digest
from quant_lab.research.strategy_service import prepare_strategy_request
from quant_lab.strategy.sandbox import StrategySpec


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default="reports/generated/daily_factor_lab")
    args = parser.parse_args()
    report = ROOT / args.report
    provenance = json.loads((report / "dataset_provenance.json").read_text(encoding="utf-8"))
    paper = ROOT / "data/state/paper_portfolio.json"
    original = file_digest(paper)
    market = ROOT / provenance["market_file"]
    original_market = file_digest(market)
    store = JobStore(ROOT)
    # Daily frequency is chosen in advance to exercise proposal generation on
    # today's ordinary session. This is an engineering fixture, not a strategy recommendation.
    spec = StrategySpec({"volatility_20": 1.}, top_n=5, exit_rank=30, max_weight=.2,
                        rebalance_frequency="D", selection_mode="affordable", cash_buffer=.02,
                        allow_star=False, allow_chinext=False)
    payload = {"name": "工程验收：日频低波动 / 假设5000元 / 非推荐", "report": args.report,
               "hypothesis": "预先指定单因子日频规则以验证完整编排；不按业绩选择，不构成交易推荐。",
               "universe": provenance["universe"], "start": "2016-01-01", "end": provenance["end_date"],
               "strategy": asdict(spec), "backtest": asdict(BacktestConfig(initial_cash=5000)),
               "walk_forward": True, "robustness": False}
    before = {s["id"] for s in store.list()}
    store.submit("strategy", prepare_strategy_request(ROOT, payload))
    strategy_job = wait_new_job(store, before)
    result = json.loads((ROOT / strategy_job["result"] / "experiment.json").read_text(encoding="utf-8"))
    assert result["validation"]["status"] == "ready"
    experiment = result["id"]
    app = page("manual-review")
    select = widget(app, "selectbox", "选择真实数据实验")
    select.select(next(label for label in select.options if experiment[:8] in label))
    widget(app, "number_input", "当前可用现金（元）").set_value(5000.)
    widget(app, "text_input", "账户快照说明（可选）").set_value("工程验收示例：假设5000元空仓，并非用户实际账户")
    before = {s["id"] for s in store.list()}
    widget(app, "button", "保存本机方案并更新数据").click().run()
    assert not app.exception and not app.error, (app.exception, app.error)
    identifier = app.session_state["manual_profile_id"]
    review_job = wait_new_job(store, before)
    status = json.loads((profile_path(ROOT, identifier) / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "ready" and status["audit_passed"]
    assert status["account_type"] == "manual_snapshot" and status["orders_actionable"] is False
    assert status["code_version"] == code_version(ROOT)
    reopened = page("manual-review", {"manual_profile_id": identifier})
    assert not reopened.exception and not reopened.error
    assert file_digest(paper) == original and file_digest(market) == original_market
    record = {"status": "passed", "strategy_job": strategy_job["id"], "review_job": review_job["id"],
              "experiment_id": experiment, "profile_id": identifier, "data_through": status["data_through"],
              "next_trade_date": status["next_trade_date"], "order_status": status["order_status"],
              "account": "hypothetical_5000_empty", "paper_unchanged": True, "historical_market_unchanged": True,
              "automatic_trading": False}
    (ROOT / "data/state/manual_flow_acceptance.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
