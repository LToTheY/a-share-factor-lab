from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from quant_lab.data.baostock_supplement import (
    expected_sessions,
    fill_gaps,
    missing_ranges,
    normalize_raw,
)
from quant_lab.data.storage_budget import StorageBudget, write_budgeted_frame


def response(dates, code="sz.000001"):
    return pd.DataFrame({
        "date": dates, "code": code, "open": "10", "high": "10.2",
        "low": "9.9", "close": "10.1", "preclose": "10", "volume": "1000",
        "amount": "10100", "turn": "1.2", "tradestatus": "1", "isST": "0",
        "adjustflag": "3",
    })


def test_raw_normalization_never_marks_unknown_status_known():
    frame = response(["2025-01-02"])
    frame["tradestatus"] = ""
    frame["isST"] = ""
    result = normalize_raw(frame)
    assert not result["is_suspended_known"].iloc[0]
    assert not result["is_st_known"].iloc[0]
    frame["adjustflag"] = "1"
    with pytest.raises(ValueError, match="adjusted"):
        normalize_raw(frame)


def test_gap_plan_finds_internal_dates_and_respects_listing_interval():
    sessions = pd.bdate_range("2025-01-01", "2025-01-10")
    expected = expected_sessions(sessions, "2025-01-01", "2025-01-10",
                                 {"ipoDate": "2025-01-02", "outDate": "2025-01-09"})
    assert expected.min() == pd.Timestamp("2025-01-02")
    assert expected.max() == pd.Timestamp("2025-01-08")
    observed = expected.delete([1, 2])
    assert missing_ranges(expected, observed) == [("2025-01-03", "2025-01-06")]
    assert missing_ranges(expected, expected) == []


def test_budgeted_frame_refuses_unbudgeted_path(tmp_path):
    with pytest.raises(ValueError, match="within"):
        write_budgeted_frame(pd.DataFrame({"x": [1]}), tmp_path.parent / "outside.parquet",
                             StorageBudget(tmp_path, 1_000_000, 0))


def test_gap_filler_reuses_local_history_and_daily_cache_and_resumes(tmp_path):
    root = tmp_path
    ref = root / "data/raw/market_reference"
    ref.mkdir(parents=True)
    pd.DataFrame({"symbol": ["000001.SZ"]}).to_parquet(ref / "historical_symbols.parquet")
    dates = pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06"])
    pd.DataFrame({"trade_date": dates, "is_trading_day": True}).to_parquet(ref / "trade_calendar.parquet")
    old = root / "data/raw/baostock_daily/raw_daily/000001_SZ.parquet"
    old.parent.mkdir(parents=True)
    normalize_raw(response(["2025-01-02"])).to_parquet(old)
    processed = root / "data/processed/baostock_daily.parquet"
    processed.parent.mkdir(parents=True)
    normalize_raw(response(["2025-01-03"])).to_parquet(processed)
    basic = root / "data/raw/baostock_supplement/reference/stock_basic.parquet"
    basic.parent.mkdir(parents=True)
    pd.DataFrame({"code": ["sz.000001"], "ipoDate": ["1991-01-01"], "outDate": [""]}).to_parquet(basic)
    calls = []

    def history(code, fields, start_date, end_date, **kwargs):
        calls.append((code, start_date, end_date))
        return response(pd.bdate_range(start_date, end_date).strftime("%Y-%m-%d"), code)

    client = SimpleNamespace(
        bs=SimpleNamespace(query_daily_history_k_AStock=lambda day: response([day]),
                           query_history_k_data_plus=history),
        _to_frame=lambda frame, _: frame,
    )
    budget = StorageBudget(root / "data", 100_000_000, 0)
    report = fill_gaps(client, root, "2025-01-02", "2025-01-06", budget,
                       progress=lambda _: None)
    assert report["status"] == "raw_gaps_filled"
    assert ("sz.000001", "2025-01-03", "2025-01-03") in calls
    assert not report["activated"] and not report["old_files_deleted"]
    initial = len(calls)
    repeated = fill_gaps(client, root, "2025-01-02", "2025-01-06", budget,
                         progress=lambda _: None)
    assert repeated["status"] == "raw_gaps_filled" and len(calls) == initial
    assert len(pd.read_parquet(old)) == 1
    assert report["benchmarks"]["sh.000905"]["data_through"] == "2025-01-06"
    # Extending the download must update existing benchmark files as well.
    pd.DataFrame({"trade_date": pd.bdate_range("2025-01-02", "2025-01-07"),
                  "is_trading_day": True}).to_parquet(ref / "trade_calendar.parquet")
    extended = fill_gaps(client, root, "2025-01-02", "2025-01-07", budget, progress=lambda _: None)
    assert extended["benchmarks"]["sh.000905"]["data_through"] == "2025-01-07"
    assert ("sh.000905", "2025-01-07", "2025-01-07") in calls


def test_gap_filler_stops_after_three_failed_requests_and_preserves_cache(tmp_path, monkeypatch):
    import quant_lab.data.baostock_supplement as supplement

    monkeypatch.setattr(supplement.time, "sleep", lambda _: None)
    ref = tmp_path / "data/raw/market_reference"
    ref.mkdir(parents=True)
    pd.DataFrame({"symbol": ["000001.SZ"]}).to_parquet(ref / "historical_symbols.parquet")
    pd.DataFrame({"trade_date": pd.bdate_range("2025-01-02", "2025-01-08"),
                  "is_trading_day": True}).to_parquet(ref / "trade_calendar.parquet")
    basic = tmp_path / "data/raw/baostock_supplement/reference"
    basic.mkdir(parents=True)
    pd.DataFrame({"code": ["sz.000001"], "ipoDate": ["1991-01-01"], "outDate": [""]}).to_parquet(basic / "stock_basic.parquet")
    old = tmp_path / "data/processed/baostock_daily.parquet"
    old.parent.mkdir(parents=True)
    normalize_raw(response(["2025-01-02"])).to_parquet(old)
    previous = old.read_bytes()
    calls = []

    def unavailable(day):
        calls.append(day)
        raise ConnectionError("test provider outage")

    client = SimpleNamespace(bs=SimpleNamespace(query_daily_history_k_AStock=unavailable),
                             _to_frame=lambda frame, _: frame)
    result = fill_gaps(client, tmp_path, "2025-01-02", "2025-01-08",
                       StorageBudget(tmp_path / "data", 10_000_000, 0), progress=lambda _: None)
    assert result["status"] == "provider_unavailable_resume_required"
    assert len(result["failures"]) == 3 and len(calls) == 9
    assert result["completed_stocks"] == 0 and old.read_bytes() == previous
