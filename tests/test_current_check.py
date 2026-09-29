from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from quant_lab.data.freshness import (
    current_result_status,
    session_target,
    validate_latest_panel,
)
from quant_lab.data.research_prices import prepare_price_panel
from quant_lab.research.current_check import run_current_check, score_current_market
from quant_lab.research.settings import load_research_settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolate_mock_network_tests_from_machine_free_space(monkeypatch):
    from quant_lab.data.storage_budget import StorageBudget

    monkeypatch.setattr("quant_lab.research.current_check.StorageBudget",
                        lambda root, **kwargs: StorageBudget(root, minimum_free_bytes=0))


def calendar():
    dates = pd.date_range("2025-01-01", "2025-02-10")
    return pd.DataFrame({"trade_date": dates, "is_trading_day": (dates.dayofweek < 5) & ~dates.isin(pd.date_range("2025-01-28", "2025-02-04"))})


def test_low_space_replaces_stale_success_without_connecting(tmp_path, monkeypatch):
    from quant_lab.data.storage_budget import StorageBudget

    monkeypatch.setattr("quant_lab.research.current_check.StorageBudget",
                        lambda root, **kwargs: StorageBudget(root, minimum_free_bytes=2000))
    monkeypatch.setattr("quant_lab.data.storage_budget.shutil.disk_usage", lambda _: SimpleNamespace(free=1000))
    path = tmp_path / "data/state/current_check.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"status":"ready","audit_passed":true}', encoding="utf-8")

    def forbidden_connection():
        pytest.fail("No network client should be constructed when the reserve fails")

    result = run_current_check(tmp_path, ROOT / "configs/research.yaml", client_factory=forbidden_connection)
    assert result["status"] == "blocked" and not result["audit_passed"]
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "blocked"
    orders = pd.read_csv(tmp_path / "reports/generated/current_check/next_day_orders.csv")
    assert orders.status.iloc[0] == "DATA_NOT_READY"


def test_target_respects_shanghai_time_and_holidays_and_rejects_expired_calendar():
    before, after = session_target(calendar(), pd.Timestamp("2025-01-27 09:30", tz="UTC"))[0], session_target(calendar(), pd.Timestamp("2025-01-27 10:30", tz="UTC"))[0]
    assert before == pd.Timestamp("2025-01-24")
    assert after == pd.Timestamp("2025-01-27")
    target, upcoming = session_target(calendar(), pd.Timestamp("2025-02-01 19:00"))
    assert target == pd.Timestamp("2025-01-27") and upcoming == pd.Timestamp("2025-02-05")
    with pytest.raises(ValueError, match="calendar"):
        session_target(calendar(), pd.Timestamp("2025-02-11"))


def raw_prices(dates, symbol="000001.SZ"):
    return pd.DataFrame({"trade_date": dates, "symbol": symbol, "open": 10.0, "high": 10.1,
                         "low": 9.9, "close": 10.0, "preclose": 10.0, "volume": 1_000_000.0,
                         "amount": 10_000_000.0, "turnover_rate": 1.0, "is_st": False,
                         "is_suspended": False, "is_st_known": True, "is_suspended_known": True,
                         "up_limit": np.nan, "down_limit": np.nan})


def test_adjustment_handles_split_and_separates_unknown_gap():
    dates = pd.bdate_range("2025-01-02", periods=5)
    raw = raw_prices(dates.delete(3))
    for col in ["open", "high", "low", "close", "preclose"]:
        raw[col] = [10.0, 5.0, 5.5, 6.0]
    raw["preclose"] = [10.0, 5.0, 5.0, 5.5]
    raw["amount"] = raw["close"] * raw["volume"]
    basic = pd.DataFrame({"code": ["sz.000001"], "ipoDate": ["1991-01-01"]})
    result = prepare_price_panel(raw, dates, basic)
    assert result["adj_close"].tolist()[:3] == [10.0, 10.0, 11.0]
    assert result["research_segment"].tolist() == [1, 1, 1, 2]
    audit = validate_latest_panel(result, dates[-1], {"000001.SZ"}, dates, lookback=5)
    assert not audit["passed"] and audit["window_gaps"][0]["missing_sessions"] == 1


def test_latest_day_alone_and_unknown_status_cannot_pass():
    dates = pd.bdate_range("2025-01-02", periods=130)
    raw = raw_prices(dates[-1:])
    raw["adj_close"] = 10.0
    raw["limit_status_known"] = False
    audit = validate_latest_panel(raw, dates[-1], {"000001.SZ"}, dates)
    assert not audit["passed"]
    assert audit["window_gaps"][0]["missing_sessions"] == 120
    assert audit["unknown_status_symbols"] == ["000001.SZ"]


def test_nullable_status_is_unknown_not_implicitly_true():
    dates = pd.bdate_range("2025-01-02", periods=5)
    market = raw_prices(dates).assign(adj_close=10., limit_status_known=True)
    market["limit_status_known"] = market.limit_status_known.astype("boolean")
    market.loc[4, "limit_status_known"] = pd.NA
    result = validate_latest_panel(market, dates[-1], {"000001.SZ"}, dates, lookback=5)
    assert not result["passed"]
    assert result["unknown_status_symbols"] == ["000001.SZ"]


def test_short_calendar_cannot_shorten_required_factor_window():
    dates = pd.bdate_range("2025-01-02", periods=5)
    market = raw_prices(dates).assign(adj_close=10., limit_status_known=True)
    result = validate_latest_panel(market, dates[-1], {"000001.SZ"}, dates, lookback=121)
    assert not result["passed"]
    assert any("交易日历" in message for message in result["errors"])


def test_explicit_suspension_with_existing_close_and_empty_activity_is_not_a_download_gap():
    dates = pd.bdate_range("2025-01-02", periods=3)
    raw = raw_prices(dates)
    raw.loc[1, ["volume", "amount"]] = np.nan
    raw.loc[1, "is_suspended"] = True
    raw.loc[2, "amount"] = np.nan  # Unconfirmed active gap must stay unknown.
    basic = pd.DataFrame({"code": ["sz.000001"], "ipoDate": ["1991-01-01"]})
    result = prepare_price_panel(raw, dates, basic)
    assert result.loc[1, "amount"] == 0 and result.loc[1, "was_suspension_filled"]
    assert pd.isna(result.loc[2, "amount"])


def test_saved_success_becomes_stale_on_next_data_session():
    status = {"status": "ready", "data_through": "2025-01-24", "next_trade_date": "2025-01-27"}
    assert current_result_status(status, calendar(), pd.Timestamp("2025-01-27 10:00"))[0]
    assert not current_result_status(status, calendar(), pd.Timestamp("2025-01-27 19:00"))[0]
    assert not current_result_status({"status": "blocked", "message": "网络失败"}, calendar())[0]


def test_suspension_adjustment_uses_exchange_preclose_after_corporate_action():
    dates = pd.bdate_range("2025-01-02", periods=4)
    raw = raw_prices(dates)
    raw.loc[1:2, ["open", "high", "low", "close"]] = 0.0
    raw.loc[1:2, ["volume", "amount"]] = np.nan
    raw.loc[1:2, "is_suspended"] = True
    raw.loc[1, "preclose"] = 5.0
    raw.loc[2, "preclose"] = np.nan
    raw.loc[3, ["open", "high", "low", "close", "preclose"]] = 5.0
    basic = pd.DataFrame({"code": ["sz.000001"], "ipoDate": ["1991-01-01"]})
    result = prepare_price_panel(raw, dates, basic)
    assert result["close"].tolist() == [10.0, 5.0, 5.0, 5.0]
    assert result["adj_close"].tolist() == [10.0, 10.0, 10.0, 10.0]


def test_connection_failure_invalidates_current_orders_without_touching_paper_holdings(tmp_path, monkeypatch):
    import quant_lab.research.current_check as workflow

    monkeypatch.setattr(workflow.time, "sleep", lambda _: None)
    state = tmp_path / "data/state/paper_portfolio.json"
    state.parent.mkdir(parents=True)
    state.write_text('{"cash":3000,"positions":{"000001.SZ":100}}', encoding="utf-8")
    old = state.read_bytes()
    orders = tmp_path / "reports/generated/current_check/next_day_orders.csv"
    orders.parent.mkdir(parents=True)
    orders.write_text("status,symbol\nREVIEW_REQUIRED,000001.SZ\n", encoding="utf-8")
    calls = []

    def fail():
        calls.append(1)
        raise ConnectionError("offline")

    client = SimpleNamespace(reconnect=fail, __exit__=lambda: None)
    result = run_current_check(tmp_path, ROOT / "configs/research.yaml", now=pd.Timestamp("2025-01-27 19:00"), client_factory=lambda: client)
    assert result["status"] == "blocked" and len(calls) == 3
    assert pd.read_csv(orders)["status"].tolist() == ["DATA_NOT_READY"]
    assert state.read_bytes() == old and not result["audit_passed"]


def test_current_factor_scoring_uses_holdings_buffer_and_no_future_labels():
    from dataclasses import replace

    from quant_lab.research.settings import FactorDefinition

    settings = replace(load_research_settings(ROOT / "configs/research.yaml"),
                       factors=(FactorDefinition("momentum_20_5", 1.0),),
                       minimum_valid_factors=1, minimum_factor_coverage=0.8,
                       min_amount=0, min_listed_days=0, top_n=2, exit_rank=3, max_weight=0.5)
    dates = pd.bdate_range("2025-01-01", periods=30)
    frames = []
    for n in range(4):
        frame = raw_prices(dates, f"00000{n + 1}.SZ")
        frame["adj_close"] = 10 * (1 + .001 * (n + 1)) ** np.arange(len(frame))
        frame["research_segment"] = 1
        frame["in_index"] = True
        frame["listing_age_sessions"] = 999
        frame["limit_status_known"] = True
        frames.append(frame)
    market = pd.concat(frames, ignore_index=True)
    signals, targets, coverage = score_current_market(market, settings, {"positions": {"000002.SZ": 100}})
    assert set(targets.symbol) == {"000002.SZ", "000004.SZ"}
    assert signals.trade_date.nunique() == 1 and coverage["momentum_20_5"] == 1.0
    assert not any("forward_return" in c for c in signals)


def test_current_ui_hides_stale_and_failed_orders(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from dashboard.views import current_check

    monkeypatch.setattr(current_check, "PROJECT_ROOT", tmp_path)
    state = tmp_path / "data/state/current_check.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"status": "blocked", "message": "缺少最新交易日"}), encoding="utf-8")
    app = AppTest.from_string("from dashboard.views.current_check import render\nrender()").run()
    assert not app.exception
    assert app.warning[0].value == "缺少最新交易日"
    assert not app.dataframe


def test_live_refresh_requeries_today_reuses_history_and_prefers_csmar(tmp_path, monkeypatch):
    import quant_lab.data.current_market as updater
    from quant_lab.data.market_staging import CSMAR_RENAME
    from quant_lab.data.storage_budget import StorageBudget

    # Snapshot size validation is tested separately with the real 300/500 sizes.
    monkeypatch.setattr(updater, "validate_snapshot", lambda frame, code, date: frame)
    dates = pd.bdate_range("2024-01-01", "2025-07-09")
    raw = raw_prices(dates)
    for column in ["pe_ttm", "pb_mrq", "ps_ttm", "pcf_ncf_ttm"]:
        raw[column] = 1.0
    old = tmp_path / "data/raw/baostock_daily/raw_daily/000001_SZ.parquet"
    old.parent.mkdir(parents=True)
    raw.to_parquet(old)
    old_bytes = old.read_bytes()
    cached_symbol = tmp_path / "data/raw/baostock_supplement/raw_daily/000001_SZ.parquet"
    cached_symbol.parent.mkdir(parents=True)
    raw.tail(1).assign(close=9.99).to_parquet(cached_symbol)
    ref = tmp_path / "data/raw/market_reference"
    ref.mkdir(parents=True)
    pd.DataFrame({"symbol": ["000001.SZ", "600000.SH"]}).to_parquet(ref / "historical_symbols.parquet")
    pd.DataFrame({"trade_date": dates, "is_trading_day": True}).to_parquet(ref / "trade_calendar.parquet")
    pd.DataFrame({"trade_date": [dates[-1]] * 3, "symbol": ["000001.SZ", "600000.SH", "000002.SZ"],
                  "index_code": ["000905.SH", "000300.SH", "000905.SH"], "snapshot_complete": True}).to_parquet(ref / "index_membership.parquet")
    cs = pd.DataFrame({c: [10.0] for c in CSMAR_RENAME if c != "trddt"})
    cs["stkcd"] = "000001"
    cs["trddt"] = ["2025-07-01"]  # WRDS partitions may store empty/date fields as strings.
    cs["dnshrtrd"] = 1_000_000.0
    cs["dnvaltrd"] = 10_000_000.0
    csdir = tmp_path / "data/raw/csmar/bulk/csmar_trade/trd_dalyr"
    csdir.mkdir(parents=True)
    cs.to_parquet(csdir / "2025-07.parquet")
    calls = []

    def cross(day):
        calls.append(day)
        frame = raw_prices(pd.to_datetime([day]))
        frame["adjustflag"] = "3"
        return frame

    def trade_calendar(first, last):
        days = pd.date_range(first, last)
        return pd.DataFrame({"trade_date": days, "is_trading_day": days.dayofweek < 5})

    client = SimpleNamespace(
        trade_calendar=trade_calendar,
        index_snapshot=lambda code, date: pd.DataFrame({"trade_date": pd.to_datetime([date]),
                                                        "symbol": ["000001.SZ" if code == "000905.SH" else "600000.SH"]}),
        _to_frame=lambda frame, _: frame,
        bs=SimpleNamespace(query_daily_history_k_AStock=cross,
                           query_history_k_data_plus=lambda code, fields, start_date, end_date, **kw: pd.DataFrame({
                               "date": pd.bdate_range(start_date, end_date).strftime("%Y-%m-%d"), "code": code,
                               "open": "10", "high": "10.1", "low": "9.9", "close": "10"}),
                           query_stock_basic=lambda: pd.DataFrame({"code": ["sz.000001", "sh.600000"],
                                                                   "ipoDate": ["1991-01-01", "1991-01-01"]})),
    )
    market, gate = updater.refresh_current_market(client, tmp_path, StorageBudget(tmp_path / "data", 100_000_000, 0),
                                                   now=pd.Timestamp("2025-07-09 19:00"), lookback_sessions=130)
    assert gate["passed"] and gate["data_through"] == "2025-07-09"
    assert calls == ["2025-07-09"]
    assert market.loc[market.trade_date.eq("2025-07-09"), "close"].iloc[0] == 10.0
    assert market.loc[market.trade_date.eq("2025-07-01"), "close_source"].iloc[0] == "csmar"
    assert old.read_bytes() == old_bytes
    assert "000002.SZ" not in set(pd.read_parquet(ref / "index_membership.parquet")["symbol"])


def test_success_publishes_dated_snapshot_and_never_changes_account(tmp_path, monkeypatch):
    import quant_lab.research.current_check as workflow

    dates = pd.bdate_range("2025-01-01", "2025-01-24")
    market = raw_prices(dates)
    signals = market.tail(1).assign(factor_processed=1.0, factor_rank=1, valid_factor_count=11)
    targets = signals[["trade_date", "symbol", "factor_processed", "factor_rank"]].assign(target_weight=0.05)
    gate = {"passed": True, "data_through": "2025-01-24", "next_trade_date": "2025-01-27", "research_symbols": 500}
    monkeypatch.setattr(workflow, "refresh_current_market", lambda *a, **k: (market, gate))
    monkeypatch.setattr(workflow, "score_current_market", lambda *a: (signals, targets, {"momentum": 1.0}))
    cal_path = tmp_path / "data/raw/baostock_supplement/reference/current_calendar.parquet"
    cal_path.parent.mkdir(parents=True)
    calendar().to_parquet(cal_path)
    state = tmp_path / "data/state/paper_portfolio.json"
    state.parent.mkdir(parents=True)
    state.write_text('{"cash":3000,"positions":{}}', encoding="utf-8")
    previous = state.read_bytes()
    client = SimpleNamespace(reconnect=lambda: None, __exit__=lambda: None)
    result = run_current_check(tmp_path, ROOT / "configs/research.yaml", now=pd.Timestamp("2025-01-24 19:00"), client_factory=lambda: client)
    assert result["status"] == "ready" and result["audit_passed"]
    assert (tmp_path / result["report_dir"] / "next_day_orders.csv").exists()
    assert result["paper_cash"] == 3000 and state.read_bytes() == previous
