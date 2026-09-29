from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from quant_lab.data.index_membership import (
    attach_shared_memberships,
    collect_memberships,
    validate_snapshot,
)
from quant_lab.data.storage_budget import StorageBudget
from quant_lab.data.wrds_access import describe_library, open_wrds


def snapshot(date, size=300):
    return pd.DataFrame({
        "trade_date": pd.Timestamp(date),
        "symbol": [f"{i:06d}.SZ" for i in range(1, size + 1)],
        "weight": float("nan"),
    })


def test_membership_rejects_current_snapshot_returned_for_history():
    with pytest.raises(ValueError, match="future"):
        validate_snapshot(snapshot("2026-09-28"), "000300.SH", "2015-01-05")
    with pytest.raises(ValueError, match="Incomplete"):
        validate_snapshot(snapshot("2015-01-05", 299), "000300.SH", "2015-01-05")


def test_two_index_masks_never_use_a_future_snapshot():
    market = pd.DataFrame({
        "trade_date": pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06"]),
        "symbol": ["000001.SZ"] * 3,
    })
    members = pd.DataFrame({
        "trade_date": pd.to_datetime(["2025-01-03", "2025-01-06", "2025-01-02"]),
        "symbol": ["000001.SZ", "000002.SZ", "000003.SZ"],
        "index_code": ["000300.SH", "000300.SH", "000905.SH"],
    })
    result = attach_shared_memberships(market, members)
    assert result["in_hs300"].tolist() == [False, True, False]
    assert result["in_csi800"].tolist() == [False, True, False]
    assert not result["in_zz500"].any()


def test_member_shortfall_is_flagged_for_acquisition_without_inventing_a_member(tmp_path):
    class Downloader:
        def index_snapshot(self, index_code, date):
            return snapshot(date, 499)

    calendar = pd.DataFrame({
        "trade_date": pd.to_datetime(["2019-01-07"]), "is_trading_day": [True],
    })
    result, counts = collect_memberships(
        Downloader(), calendar, tmp_path / "reference", tmp_path / "legacy",
        ["000905.SH"], allow_one_missing_for_download=True,
    )
    assert len(result) == 499
    assert counts["incomplete_snapshot_requests"] == 1
    assert not result["snapshot_complete"].any()
    assert result["membership_quality"].eq("incomplete_provider_snapshot").all()
    with pytest.raises(ValueError, match="download scope only"):
        attach_shared_memberships(snapshot("2019-01-07", 499), result)
    with pytest.raises(ValueError, match="Incomplete"):
        validate_snapshot(snapshot("2019-01-07", 498), "000905.SH", "2019-01-07",
                          allow_one_missing_for_download=True)
    with pytest.raises(ValueError, match="future"):
        validate_snapshot(snapshot("2019-01-08", 499), "000905.SH", "2019-01-07",
                          allow_one_missing_for_download=True)


def test_membership_cache_is_resumable_and_includes_first_session(tmp_path):
    class Downloader:
        def __init__(self):
            self.calls = []

        def index_snapshot(self, index_code, date):
            self.calls.append((index_code, date))
            return snapshot(date)

    downloader = Downloader()
    calendar = pd.DataFrame({
        "trade_date": pd.bdate_range("2025-01-06", "2025-01-17"),
        "is_trading_day": True,
    })
    args = (downloader, calendar, tmp_path / "reference", tmp_path / "legacy", ["000300.SH"])
    first, counts = collect_memberships(*args)
    assert counts["downloaded_snapshots"] == 3
    assert downloader.calls[0][1] == "2025-01-06"
    second, counts = collect_memberships(*args)
    assert counts["reused_snapshots"] == 3
    assert len(downloader.calls) == 3
    pd.testing.assert_frame_equal(first, second)


def test_storage_budget_accounts_for_temporary_copy_and_disk_reserve(tmp_path, monkeypatch):
    (tmp_path / "existing").write_bytes(b"x" * 60)
    monkeypatch.setattr(
        "quant_lab.data.storage_budget.shutil.disk_usage",
        lambda _: SimpleNamespace(free=100),
    )
    budget = StorageBudget(tmp_path, maximum_bytes=100, minimum_free_bytes=50)
    assert budget.check(40)["data_bytes"] == 60
    with pytest.raises(RuntimeError, match="budget"):
        budget.check(41)
    with pytest.raises(RuntimeError, match="reserve"):
        StorageBudget(tmp_path, 1000, 90).check(11)


def test_catalog_uses_bound_library_and_returns_only_metadata():
    class Connection:
        def list_libraries(self):
            return ["csmar"]

        def raw_sql(self, sql, params, chunksize):
            assert "%(library)s" in sql
            assert params == {"library": "csmar"}
            assert chunksize is None
            return pd.DataFrame([{
                "table_name": "prices", "column_name": "date",
                "data_type": "date", "comment": None,
            }])

    result = describe_library(Connection(), "csmar")
    assert result == {"tables": {"prices": [{
        "name": "date", "type": "date", "comment": None,
    }]}, "table_count": 1}
    with pytest.raises(ValueError, match="not visible"):
        describe_library(Connection(), "unsubscribed")


def test_wrds_auth_never_invokes_pgpass_flow_and_closes_on_error(monkeypatch):
    wrds = pytest.importorskip("wrds")
    sa = pytest.importorskip("sqlalchemy")
    calls = []

    class Connection:
        def close(self):
            calls.append("close")

    class Engine:
        def connect(self):
            return Connection()

        def dispose(self):
            calls.append("dispose")

    class Client:
        def __init__(self, autoconnect, wrds_username):
            assert autoconnect is False
            assert wrds_username == "test-user"

        def load_library_list(self):
            calls.append("catalog")

        def connect(self):
            pytest.fail("Default WRDS password-save flow must not run")

    def create_engine(url, **kwargs):
        assert url.password == "test-only-placeholder"
        assert "default_transaction_read_only=on" in kwargs["connect_args"]["options"]
        return Engine()

    monkeypatch.setattr(wrds, "Connection", Client)
    monkeypatch.setattr(sa, "create_engine", create_engine)
    monkeypatch.setattr(
        "quant_lab.data.wrds_access.getpass.getpass",
        lambda _: "test-only-placeholder",
    )
    with pytest.raises(ValueError, match="abort"), open_wrds("test-user"):
        raise ValueError("abort")
    assert calls == ["catalog", "close", "dispose"]
