from __future__ import annotations

import json

import duckdb
import numpy as np
import pandas as pd
import pytest

from quant_lab.backtest.engine import run_backtest
from quant_lab.data.baostock_supplement import normalize_raw, read_symbol_sources
from quant_lab.data.index_membership import attach_shared_memberships
from quant_lab.data.market_dataset import archive_baostock, require_matching_dataset
from quant_lab.data.market_staging import _register_baostock
from quant_lab.data.storage_budget import StorageBudget
from quant_lab.evaluation.diagnostics import add_forward_returns
from quant_lab.factors.library import compute_factor


def test_archive_survives_legacy_removal_and_keeps_newest_priority(tmp_path):
    legacy = tmp_path / "data/raw/baostock_daily/raw_daily/a.parquet"
    legacy.parent.mkdir(parents=True)
    old = normalize_raw(pd.DataFrame({"date": ["2025-01-02", "2025-01-03"], "code": "sz.000001",
                                      "open": "10", "high": "10", "low": "10", "close": "10",
                                      "preclose": "10", "volume": "100", "amount": "1000",
                                      "isST": "0", "tradestatus": "1", "adjustflag": "3"}))
    old.to_parquet(legacy, index=False)
    result = archive_baostock(tmp_path, StorageBudget(tmp_path / "data", 10_000_000, 0))
    assert result["rows"] == 2 and result["source_values_verified"]
    legacy.unlink()
    with duckdb.connect() as connection:
        _register_baostock(connection, tmp_path)
        assert connection.execute("SELECT count(*) FROM bao").fetchone()[0] == 2
    recovered = read_symbol_sources(tmp_path, tmp_path / "data/raw/baostock_supplement", "000001.SZ", pd.DataFrame())
    assert recovered["close"].eq(10).all()


def test_observed_subset_does_not_invent_missing_member_or_backfill_future():
    members = pd.DataFrame({"trade_date": pd.to_datetime(["2025-01-03"] * 2 + ["2025-01-06"]),
                            "symbol": ["A", "B", "C"], "index_code": ["000300.SH", "000905.SH", "000905.SH"],
                            "snapshot_complete": [True, False, True]})
    market = pd.DataFrame({"trade_date": pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-03", "2025-01-06"]),
                           "symbol": ["B", "B", "C", "C"]})
    with pytest.raises(ValueError, match="Incomplete"):
        attach_shared_memberships(market, members)
    result = attach_shared_memberships(market, members, allow_observed_subset=True)
    assert result["in_zz500"].tolist() == [False, True, False, True]
    assert result["zz500_membership_complete"].tolist() == [False, False, False, True]


def test_factors_and_forward_labels_do_not_bridge_unobserved_price_segments():
    frame = pd.DataFrame({"trade_date": pd.bdate_range("2025-01-01", periods=12), "symbol": "A",
                          "close": [10.0] * 6 + [100.0] * 6, "research_segment": [1] * 6 + [2] * 6})
    factor = compute_factor(frame, "reversal_5")
    assert factor["factor"].iloc[6:11].isna().all()
    assert factor["factor"].iloc[-1] == 0
    labels = add_forward_returns(frame, 1)
    assert np.isnan(labels.loc[5, "forward_return_1d"])


def test_market_score_version_mismatch_is_rejected(tmp_path):
    market = tmp_path / "market.parquet"
    market.with_suffix(".manifest.json").write_text(json.dumps({"dataset_id": "new"}))
    scores = tmp_path / "factor_scores.parquet"
    with pytest.raises(ValueError, match="版本不一致"):
        require_matching_dataset(scores, market)
    (tmp_path / "dataset_provenance.json").write_text(json.dumps({"dataset_id": "new"}))
    require_matching_dataset(scores, market)


@pytest.mark.parametrize("flag", ["is_usable_market_data", "limit_status_known", "is_suspended_known"])
def test_backtest_blocks_flagged_or_unknown_execution_rows(flag):
    dates = pd.bdate_range("2025-01-01", periods=3)
    market = pd.DataFrame({"trade_date": dates, "symbol": "A", "open": 10.0, "close": 10.0, flag: [True, False, True]})
    targets = pd.DataFrame({"trade_date": [dates[0]], "symbol": ["A"], "target_weight": [1.0]})
    result = run_backtest(market, targets)
    assert result.trades.empty
    assert len(result.execution_issues) == 1
