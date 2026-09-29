from __future__ import annotations

import pandas as pd
import pytest

from quant_lab.data.baostock_supplement import normalize_raw
from quant_lab.data.market_staging import (
    CSMAR_RENAME,
    build_raw_staging,
    merge_raw_sources,
    normalize_csmar,
)
from quant_lab.data.storage_budget import StorageBudget


def sources():
    cs = pd.DataFrame({name: [10.0] for name in CSMAR_RENAME if name != "trddt"})
    cs["trddt"] = "2024-12-31"
    cs["stkcd"] = "000001"
    cs["clsprc"] = 11.0
    cs["hiprc"] = 12.0
    bao = pd.DataFrame({
        "date": ["2024-12-31", "2025-01-02"], "code": "sz.000001", "open": "10",
        "high": "12", "low": "9", "close": "10", "preclose": "10", "volume": "100",
        "amount": "1000", "tradestatus": "1", "isST": "0", "adjustflag": "3",
    })
    return normalize_csmar(cs, {"000001": "000001.SZ"}), normalize_raw(bao)


def test_csmar_priority_bao_recent_fallback_and_raw_only_contract():
    cs, bao = sources()
    result, comparison = merge_raw_sources(cs, bao)
    assert result["close"].tolist() == [11.0, 10.0]
    assert result["close_source"].tolist() == ["csmar", "baostock"]
    assert comparison["close"]["different"] == 1
    assert result["status_source"].eq("baostock").all()
    assert not result["research_ready"].any()
    assert result["price_adjustment"].eq("raw").all()
    assert not result["limit_status_known"].iloc[1]
    assert "adj_factor" not in result and "market_cap" not in result


def test_csmar_only_has_unknown_bao_status_and_per_field_fallback():
    cs, bao = sources()
    cs["preclose"] = float("nan")
    result, _ = merge_raw_sources(cs, bao)
    assert result["preclose_source"].iloc[0] == "baostock"
    result, _ = merge_raw_sources(cs, bao.iloc[0:0])
    assert result["status_source"].iloc[0] == "unknown"
    assert not result["is_st_known"].iloc[0]


def test_merge_rejects_duplicate_input_instead_of_cartesian_expansion():
    cs, bao = sources()
    with pytest.raises(ValueError, match="Duplicate"):
        merge_raw_sources(cs, pd.concat([bao, bao]))


def test_local_staging_build_preserves_old_files_and_reports_yearly_gaps(tmp_path):
    cs, bao = sources()
    ref = tmp_path / "data/raw/market_reference"
    ref.mkdir(parents=True)
    pd.DataFrame({"symbol": ["000001.SZ"]}).to_parquet(ref / "historical_symbols.parquet")
    pd.DataFrame({"trade_date": pd.to_datetime(["2024-12-31", "2025-01-02", "2025-01-03"]),
                  "is_trading_day": True}).to_parquet(ref / "trade_calendar.parquet")
    basic = tmp_path / "data/raw/baostock_supplement/reference"
    basic.mkdir(parents=True)
    pd.DataFrame({"code": ["sz.000001"], "ipoDate": ["1991-01-01"], "outDate": [""]}).to_parquet(basic / "stock_basic.parquet")
    old = tmp_path / "data/raw/baostock_daily/raw_daily/000001_SZ.parquet"
    old.parent.mkdir(parents=True)
    bao.to_parquet(old)
    previous = old.read_bytes()
    raw_cs = cs.rename(columns={v: k for k, v in CSMAR_RENAME.items()}).drop(columns="symbol")
    raw_cs["stkcd"] = "000001"
    cs_dir = tmp_path / "data/raw/csmar/bulk/csmar_trade/trd_dalyr"
    cs_dir.mkdir(parents=True)
    raw_cs.to_parquet(cs_dir / "2024-12.parquet")
    raw_cs.iloc[0:0].to_parquet(cs_dir / "2025-01.parquet")
    report = build_raw_staging(tmp_path, "2024-12-31", "2025-01-03", StorageBudget(tmp_path / "data", 10_000_000, 0))
    assert report["rows"] == 2 and report["missing_merged_sessions"] == 1
    assert report["close_from_csmar"] == 1 and report["close_from_baostock"] == 1
    assert not report["activated"] and not report["ready_for_backtest"]
    assert old.read_bytes() == previous
    assert len(report["partitions"]) == 2
