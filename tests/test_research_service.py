"""Operational failure modes and research timing invariants."""

import json
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from quant_lab.backtest.engine import BacktestConfig, run_backtest
from quant_lab.backtest.lot_rules import round_order
from quant_lab.data.synthetic import make_synthetic_daily_data
from quant_lab.factors.custom_loader import refresh_custom_factors
from quant_lab.research.jobs import JobStore, atomic_json, execute_job
from quant_lab.research.service import code_version, dataset_version, load_inputs
from quant_lab.research.settings import FactorDefinition, load_research_settings
from quant_lab.strategy.sandbox import StrategySpec, run_strategy
from quant_lab.strategy.validation import walk_forward_strategy

ROOT = Path(__file__).resolve().parents[1]


def test_atomic_status_retries_windows_reader_lock(tmp_path, monkeypatch):
    original = Path.replace
    attempts = []
    def replace_file(self, target):
        attempts.append(1)
        if len(attempts) < 3:
            raise PermissionError("reader still open")
        return original(self, target)
    monkeypatch.setattr(Path, "replace", replace_file)
    atomic_json(tmp_path / "state.json", {"state": "succeeded"})
    assert len(attempts) == 3
    assert json.loads((tmp_path / "state.json").read_text())["state"] == "succeeded"


def test_missing_held_quote_does_not_erase_position_value():
    dates = pd.bdate_range("2025-01-01", periods=3)
    market = pd.DataFrame([{"trade_date": d, "symbol": s, "open": 10., "close": 10.}
                           for d, s in [(dates[0], "A"), (dates[1], "A"), (dates[2], "B")]])
    targets = pd.DataFrame({"trade_date": [dates[0]], "symbol": ["A"], "target_weight": [1.]})
    result = run_backtest(market, targets, BacktestConfig(initial_cash=5000, slippage_bps=0, minimum_commission=0, commission_rate=0, transfer_fee_rate=0))
    assert result.equity.equity.tolist() == [5000, 5000, 5000]
    assert result.positions.iloc[-1].symbol == "A"
    assert "VALUATION" in result.execution_issues.side.to_list()


def test_jobs_dedupe_cancel_failure_keep_existing_results(tmp_path):
    storage = JobStore(tmp_path)
    first = storage.submit("research", {"bad": True}, launch=False)
    assert storage.submit("research", {"bad": True}, launch=False)["id"] == first["id"]
    with pytest.raises(ValueError, match="重型任务"):
        storage.submit("strategy", {}, launch=False)
    storage.cancel(first["id"])
    execute_job(tmp_path, first["id"])
    assert storage.get(first["id"])["state"] == "cancelled"
    good = tmp_path / "reports/generated/old/summary.json"
    good.parent.mkdir(parents=True)
    good.write_text("successful", encoding="utf-8")
    failed = storage.submit("research", {"bad": True}, launch=False)
    execute_job(tmp_path, failed["id"])
    assert storage.get(failed["id"])["state"] == "failed"
    assert good.read_text() == "successful"
    assert "result" not in storage.get(failed["id"])


def test_cancel_during_computation_never_publishes_candidate(tmp_path, monkeypatch):
    from quant_lab.research import jobs
    storage = JobStore(tmp_path)
    payload = {"config": {}, "dataset": {}, "code_version": code_version(tmp_path), "factor_version": ""}
    status = storage.submit("research", payload, launch=False)
    def compute(root, request, output, progress):
        (output / "partial.txt").write_text("incomplete")
        storage.cancel(status["id"])
        progress("finish current unit")
    monkeypatch.setattr(jobs, "run_research", compute)
    execute_job(tmp_path, status["id"])
    assert storage.get(status["id"])["state"] == "cancelled"
    assert not (tmp_path / "reports/generated/jobs" / status["id"]).exists()


def test_pool_change_restandardizes_the_eligible_cross_section(tmp_path):
    from quant_lab.evaluation.preprocess import preprocess_factor
    from quant_lab.research.factor_suite import _prepare_market
    settings = replace(load_research_settings(ROOT / "configs/research.yaml"), min_listed_days=1, min_amount=0, require_known_status=False)
    frame = pd.DataFrame({"trade_date": pd.to_datetime(["2025-01-02"]*4), "symbol": list("ABCD"),
                          "close": [1., 2., 3., 100.], "amount": 100., "in_index": [True]*4})
    def scores(panel):
        prepared = _prepare_market(panel, settings)
        prepared["factor"] = prepared.close.where(prepared.in_universe)
        return preprocess_factor(prepared, neutralize_size=False, neutralize_industry=False).factor_processed
    union = scores(frame)
    smaller = scores(frame.assign(in_index=[True, True, True, False]))
    assert pd.isna(smaller.iloc[-1])
    assert smaller.iloc[1] == pytest.approx(0.)
    assert union.iloc[1] != pytest.approx(smaller.iloc[1])


def test_hash_mismatch_and_missing_data_rejected(tmp_path):
    path = tmp_path / "prices.parquet"
    with pytest.raises(ValueError, match="缺少行情"):
        dataset_version(path)
    pd.DataFrame({"x": [1]}).to_parquet(path)
    path.with_suffix(".manifest.json").write_text(json.dumps({"price_research_ready": True, "sha256": "wrong"}))
    with pytest.raises(ValueError, match="校验和"):
        dataset_version(path)


def test_custom_required_fields_warmup_and_pool(tmp_path):
    refresh_custom_factors()
    frame = make_synthetic_daily_data(10, 180)
    for field in ("close", "high", "low"):
        frame[f"adj_{field}"] = frame[field]
    frame["in_hs300"] = frame.symbol.str.startswith("00000")
    frame["in_zz500"] = ~frame.in_hs300
    frame["listing_age_sessions"] = 500
    path = tmp_path / "market.parquet"
    frame.to_parquet(path)
    dates = sorted(frame.trade_date.unique())
    settings = replace(load_research_settings(ROOT / "configs/research.yaml"), preferred_index="000300.SH",
                       research_start_date=str(pd.Timestamp(dates[30]).date()), research_end_date=str(pd.Timestamp(dates[-1]).date()),
                       factors=(FactorDefinition("range_position_20", 1),), minimum_valid_factors=1)
    loaded = load_inputs(path, settings)
    assert {"adj_high", "adj_low", "adj_close"}.issubset(loaded.columns)
    assert loaded.trade_date.min() == dates[10]
    assert loaded.in_index.equals(loaded.in_hs300)
    changed = load_inputs(path, replace(settings, preferred_index="000905.SH"))
    assert not changed.in_index.equals(loaded.in_index)
    with pytest.raises(ValueError, match="预热历史不足"):
        load_inputs(path, replace(settings, research_start_date=str(pd.Timestamp(dates[5]).date())))
    frame.drop(columns="adj_high").to_parquet(path)
    with pytest.raises(ValueError, match="adj_high"):
        load_inputs(path, settings)


def test_small_capital_and_board_minimum_actual_holdings():
    dates = pd.bdate_range("2025-01-01", periods=8)
    market = pd.DataFrame([{"trade_date": date, "symbol": symbol, "open": price, "close": price,
                            "adj_close": price, "in_index": True}
                           for date in dates for symbol, price in [("688001.SH", 30), ("600001.SH", 1000)]])
    scores = market[["trade_date", "symbol"]].assign(in_universe=True, alpha=[1, -1]*8)
    result = run_strategy(scores, market, StrategySpec({"alpha": 1}, top_n=1, exit_rank=1, max_weight=1, rebalance_frequency="D"), BacktestConfig(initial_cash=5000))
    assert result.backtest.trades.empty
    assert result.diagnostics["average_holdings"] == 0
    assert result.diagnostics["average_target_holdings"] == 1
    assert result.diagnostics["average_cash_ratio"] == 1
    assert result.diagnostics["unfilled_events"] > 0
    assert round_order(201, "688001.SH") == 201
    assert round_order(199, "688001.SH") == 0
    assert round_order(199, "688001.SH", holding=199) == 199
    targets = pd.DataFrame({"trade_date": [dates[0]], "symbol": ["688001.SH"], "target_weight": [1.]})
    filled = run_backtest(market, targets, BacktestConfig(initial_cash=6050, slippage_bps=0, minimum_commission=0, commission_rate=0, transfer_fee_rate=0))
    assert filled.trades.iloc[0].shares == 201
    assert filled.trades.iloc[0].trade_date > dates[0]


def test_own_strategy_walk_forward_and_embargo(tmp_path):
    frame = make_synthetic_daily_data(12, 850, "2020-01-01")
    frame["adj_close"] = frame.close
    frame["in_index"] = True
    scores = frame[["trade_date", "symbol"]].assign(in_universe=True, alpha=frame.close)
    spec = StrategySpec({"alpha": -1}, top_n=2, exit_rank=20, max_weight=.5)
    config = BacktestConfig(initial_cash=5000)
    result = walk_forward_strategy(scores, frame, spec, config, tmp_path, train_years=1, validation_years=1, test_years=1, horizon=5, embargo=1)
    assert result["status"] == "ready"
    assert result["strategy"]["factor_weights"] == {"alpha": -1}
    assert result["backtest"]["initial_cash"] == 5000
    assert result["embargo"] == 6  # next-open entry plus five held sessions
    dates = pd.DatetimeIndex(sorted(scores.trade_date.unique()))
    fold = result["folds"][0]
    assert dates.searchsorted(fold["test_start"]) - dates.searchsorted(fold["validation_end"]) > 5
    short = walk_forward_strategy(scores, frame, spec, config, tmp_path / "short")
    assert short["status"] == "insufficient_history"
