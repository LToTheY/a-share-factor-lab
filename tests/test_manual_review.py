"""Manual-account isolation, fee budgets and fail-closed source validation."""

import json
from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest
import yaml
from streamlit.testing.v1 import AppTest
from test_experiment_history import sample_experiment

from quant_lab.backtest.engine import BacktestConfig
from quant_lab.dashboard.experiments import ExperimentStore
from quant_lab.portfolio.order_plan import estimate_orders, validate_account
from quant_lab.research.manual_review import (
    create_profile,
    load_profile,
    resolve_report,
)
from quant_lab.research.service import file_digest

ROOT = Path(__file__).resolve().parents[1]


def test_order_estimates_include_fees_and_cannot_spend_unavailable_cash():
    targets = pd.DataFrame({"symbol": ["600001.SH"], "target_weight": [1.], "factor_rank": [1]})
    state = {"cash": 1000., "positions": {}}
    original = deepcopy(state)
    rows = estimate_orders(targets, state, {"600001.SH": 10.}, "2025-01-02", "2025-01-03", BacktestConfig())
    assert rows[0]["status"] == "UNFILLED"
    assert rows[0]["shares"] == 0
    assert rows[0]["estimated_cash_after"] == 1000
    assert state == original
    state["cash"] = 1006.
    row = estimate_orders(targets, state, {"600001.SH": 10.}, "2025-01-02", "2025-01-03", BacktestConfig())[0]
    assert row["shares"] == 100
    assert 0 <= row["estimated_cash_after"] < 1


def test_sell_before_buy_is_explicitly_conditional_and_odd_lots_respected():
    targets = pd.DataFrame({"symbol": ["000002.SZ"], "target_weight": [.8], "factor_rank": [1]})
    state = {"cash": 0., "positions": {"688001.SH": 215}}
    rows = estimate_orders(targets, state, {"688001.SH": 10., "000002.SZ": 10.}, "2025-01-02", "2025-01-03", BacktestConfig())
    assert rows[0]["side"] == "SELL" and rows[0]["shares"] == 215
    assert rows[1]["side"] == "BUY" and rows[1]["conditional_on_sells"]
    assert all(r["estimated_cash_after"] >= 0 for r in rows)


@pytest.mark.parametrize("state", [{"cash": -1, "positions": {}}, {"cash": 1, "positions": {"A": .5}}, {"cash": float("nan"), "positions": {}}, {"cash": 1, "positions": {"A": float("inf")}}])
def test_invalid_account_values_are_rejected(state):
    with pytest.raises(ValueError):
        validate_account(state)


def prepare_experiment(root, monkeypatch, provider="csmar_baostock"):
    import quant_lab.research.manual_review as workflow
    monkeypatch.setattr(workflow, "factor_version", lambda _: "test_factor_version")
    report = root / "reports/archive/source"
    report.mkdir(parents=True)
    scores = report / "factor_scores.parquet"
    pd.DataFrame({"alpha": [1., 2.]}).to_parquet(scores)
    source = {"provider": provider, "universe": "000905.SH", "factor_version": "test_factor_version",
              "dataset_id": "fixture_dataset", "score_sha256": file_digest(scores),
              "request": {"report": "reports/generated/old_path"}}
    (report / "dataset_provenance.json").write_text(json.dumps(source), encoding="utf-8")
    config = yaml.safe_load((ROOT / "configs/research.yaml").read_text(encoding="utf-8"))
    config["factors"]["definitions"] = [{"name": "alpha", "direction": -1.}]
    (report / "effective_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    result, spec, costs, _ = sample_experiment()
    store = ExperimentStore(root / "data/state/strategy_experiments")
    return store.save(result, spec, costs, source, "fixture"), source, report


def test_profile_keeps_default_account_and_resolves_archived_report(tmp_path, monkeypatch):
    identifier, source, report = prepare_experiment(tmp_path, monkeypatch)
    default = tmp_path / "data/state/paper_portfolio.json"
    default.write_text('{"cash":1000000,"positions":{}}', encoding="utf-8")
    original = default.read_bytes()
    assert resolve_report(tmp_path, source) == report
    profile = create_profile(tmp_path, identifier, {"cash": 3000., "positions": {"000001.SZ": 100}})
    config = yaml.safe_load((tmp_path / profile["config_file"]).read_text(encoding="utf-8"))
    assert config["factors"]["definitions"] == [{"name": "alpha", "direction": -1.}]
    assert config["signal_strategy"]["factor_weights"] == {"alpha": 1.}
    assert default.read_bytes() == original
    assert load_profile(tmp_path, profile["id"])["automatic_trading"] is False
    (report / "factor_scores.parquet").write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="快照"):
        resolve_report(tmp_path, source)


def test_synthetic_experiment_cannot_create_live_review(tmp_path, monkeypatch):
    identifier, _, _ = prepare_experiment(tmp_path, monkeypatch, "synthetic")
    with pytest.raises(ValueError, match="真实数据"):
        create_profile(tmp_path, identifier, {"cash": 5000, "positions": {}})
    assert not (tmp_path / "data/state/manual_review").exists()


def test_manual_review_empty_page_explains_next_action(tmp_path, monkeypatch):
    from dashboard.views import manual_review
    monkeypatch.setattr(manual_review, "PROJECT_ROOT", tmp_path)
    app = AppTest.from_string("from dashboard.views.manual_review import render\nrender()").run()
    assert not app.exception
    assert any("真实数据" in item.value for item in app.info)


def test_holdings_table_rejects_incomplete_or_duplicate_positions():
    from dashboard.views.manual_review import positions_from_table
    assert positions_from_table(pd.DataFrame(columns=["股票代码", "持仓股数"])) == {}
    valid = pd.DataFrame({"股票代码": ["000001.sz", "600000.SH"], "持仓股数": [100, 200]})
    assert positions_from_table(valid) == {"000001.SZ": 100, "600000.SH": 200}
    valid.loc[1, "股票代码"] = "000001.SZ"
    with pytest.raises(ValueError, match="重复"):
        positions_from_table(valid)
    valid.loc[1, "股票代码"] = None
    with pytest.raises(ValueError, match="每一行"):
        positions_from_table(valid)


def test_manual_page_hides_orders_after_account_or_code_change(tmp_path, monkeypatch):
    from dashboard.views import manual_review
    identifier, _, _ = prepare_experiment(tmp_path, monkeypatch)
    profile = create_profile(tmp_path, identifier, {"cash": 5000, "positions": {}})
    folder = tmp_path / "data/state/manual_review" / profile["id"]
    report = tmp_path / "reports/generated/manual_review" / profile["id"] / "runs/test"
    report.mkdir(parents=True)
    pd.DataFrame([{"symbol": "000001.SZ", "side": "BUY", "shares": 100, "status": "REVIEW_REQUIRED"}]).to_csv(report / "next_day_orders.csv", index=False)
    pd.DataFrame([{"symbol": "000001.SZ", "factor_rank": 1}]).to_csv(report / "latest_signal.csv", index=False)
    status = {"data_through": "2025-01-02", "next_trade_date": "2025-01-03",
              "config_sha256": file_digest(tmp_path / profile["config_file"]),
              "paper_state_sha256": file_digest(tmp_path / profile["account_file"]),
              "report_dir": str(report.relative_to(tmp_path)), "code_version": "version_one"}
    (folder / "status.json").write_text(json.dumps(status), encoding="utf-8")
    monkeypatch.setattr(manual_review, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(manual_review, "jobs_panel", lambda *_: None)
    monkeypatch.setattr(manual_review, "current_result_status", lambda *_, **__: (True, "数据检查通过"))
    monkeypatch.setattr(manual_review, "code_version", lambda _: "version_one")
    app = AppTest.from_string("from dashboard.views.manual_review import render\nrender()").run()
    result_tables = lambda: [item for item in app.dataframe if "symbol" in item.value.columns]
    assert not app.exception and len(result_tables()) == 2
    assert result_tables()[0].value.iloc[0].symbol == "000001.SZ"
    monkeypatch.setattr(manual_review, "code_version", lambda _: "version_two")
    app.run()
    assert not app.exception and not result_tables()
    assert any("代码已变化" in item.value for item in app.warning)
    monkeypatch.setattr(manual_review, "code_version", lambda _: "version_one")
    (tmp_path / profile["account_file"]).write_text('{"cash": 4000, "positions": {}}', encoding="utf-8")
    app.run()
    assert not app.exception and not result_tables()
    assert any("账户快照已变化" in item.value for item in app.warning)
