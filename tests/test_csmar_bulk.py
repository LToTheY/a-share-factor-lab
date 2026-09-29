from __future__ import annotations

import importlib.util
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from quant_lab.data.csmar_bulk import (
    BENCHMARKS,
    BULK_TABLES,
    COMPANIES,
    MAX_PARTITION_ROWS,
    build_partitions,
    download_bulk,
    partition_windows,
    query_for,
    validate_catalog,
)
from quant_lab.data.csmar_sample import SampleTable
from quant_lab.data.storage_budget import StorageBudget


def catalog():
    libraries = {}
    for spec in BULK_TABLES:
        libraries.setdefault(spec.library, {"tables": {}})["tables"][spec.table] = [
            {"name": column, "type": "date" if column == spec.date_column else "numeric",
             "comment": column} for column in spec.columns
        ]
    return {"libraries": libraries}


class Connection:
    def __init__(self, fail_at=None):
        self.calls = []
        self.fail_at = fail_at

    def raw_sql(self, sql, params, chunksize, coerce_float):
        spec = next(spec for spec in BULK_TABLES if f'"{spec.table}"' in sql)
        self.calls.append((spec.key, params))
        assert "SELECT *" not in sql
        assert "000001" not in sql
        assert "IN %(codes)s" in sql and "LIMIT %(limit)s" in sql
        assert params["limit"] == MAX_PARTITION_ROWS + 1
        assert chunksize is None and coerce_float is False
        if spec == COMPANIES:
            assert "BETWEEN" not in sql
            assert "start" not in params
        else:
            assert "BETWEEN %(start)s AND %(end)s" in sql
            assert isinstance(params["start"], date)
        if spec.key == self.fail_at:
            raise ConnectionError("simulated connection error")
        codes = params["codes"]
        values = {name: [Decimal("1234567890123.12")] * len(codes) for name in spec.columns}
        values[spec.code_column] = list(codes)
        values[spec.date_column] = [params.get("start", date(2000, 1, 1))] * len(codes)
        if "declaredate" in values:
            values["declaredate"] = ["2026-04-30"] * len(codes)
        return pd.DataFrame(values)


def arguments(tmp_path):
    return (
        catalog(), ("000001", "600000"), ("000300", "000905"),
        "2025-01-01", "2025-01-31", tmp_path / "raw/csmar/bulk",
        StorageBudget(tmp_path, 100_000_000, 0),
    )


def script():
    path = Path(__file__).resolve().parents[1] / "scripts/download_csmar_bulk.py"
    spec = importlib.util.spec_from_file_location("bulk_script_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_full_scope_is_partitioned_without_date_gaps_or_overlaps():
    parts = build_partitions(("000001",), ("000300", "000905"), "2015-01-01", "2026-09-28")
    assert len(parts) == 508
    monthly = list(partition_windows("2024-01-17", "2025-02-13", True))
    days = pd.DatetimeIndex([])
    for first, last in monthly:
        assert (pd.Timestamp(last) - pd.Timestamp(first)).days < 31
        days = days.append(pd.date_range(first, last))
    assert days.equals(pd.date_range("2024-01-17", "2025-02-13"))
    assert ("2024-02-01", "2024-02-29") in monthly
    assert list(partition_windows("2024-02-29", "2025-01-10", False)) == [
        ("2024-02-29", "2024-12-31"), ("2025-01-01", "2025-01-10"),
    ]


def test_preflight_refuses_incomplete_catalog_bad_codes_and_unknown_identifiers():
    source = catalog()
    source["libraries"][COMPANIES.library]["tables"].pop(COMPANIES.table)
    with pytest.raises(ValueError, match="Catalog missing"):
        validate_catalog(source)
    with pytest.raises(ValueError, match="six ASCII"):
        build_partitions(("000001;DROP",), ("000300",), "2025-01-01", "2025-01-31")
    with pytest.raises(ValueError, match="nonempty"):
        build_partitions((), ("000300",), "2025-01-01", "2025-01-31")
    with pytest.raises(ValueError, match="allowlist"):
        query_for(SampleTable("other", "everything", "code", "date", ("code",)))


def test_download_preserves_raw_values_and_reports_actual_lag_without_activation(tmp_path):
    connection = Connection()
    args = arguments(tmp_path)
    report = download_bulk(connection, *args, progress=lambda _: None)
    assert len(connection.calls) == 11
    assert report["completed_partitions"] == report["expected_partitions"] == 11
    assert report["status"] == "downloaded_pending_normalization_and_gap_review"
    assert not any(report[key] for key in [
        "activated", "legacy_market_data_deleted", "baostock_market_data_downloaded",
        "units_verified", "financial_point_in_time_verified",
    ])
    daily = report["coverage"][BULK_TABLES[0].key]
    assert daily["max_date"] == "2025-01-01"  # Requested Jan 31 is not invented.
    financial = pd.read_parquet(args[-2] / "csmar_financial/fs_combas/2025.parquet")
    assert financial["a001000000"].iloc[0] == Decimal("1234567890123.12")
    assert financial["declaredate"].iloc[0] == "2026-04-30"
    benchmark = next(params for table, params in connection.calls if table == BENCHMARKS.key)
    assert benchmark["codes"] == ("000300", "000905")
    again = download_bulk(connection, *args, progress=lambda _: None)
    assert len(connection.calls) == 11
    assert again["reused_partitions"] == 11


def test_interruption_is_resumable_and_never_removes_old_prices(tmp_path):
    old = tmp_path / "processed/baostock_daily.parquet"
    old.parent.mkdir()
    old.write_bytes(b"existing research data")
    args = arguments(tmp_path)
    with pytest.raises(ConnectionError):
        download_bulk(Connection(BULK_TABLES[1].key), *args, progress=lambda _: None)
    manifest = json.loads((args[-2] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "interrupted"
    assert manifest["completed_partitions"] == 1
    assert manifest["error_type"] == "ConnectionError"
    assert "simulated connection error" not in json.dumps(manifest)
    connection = Connection()
    resumed = download_bulk(connection, *args, progress=lambda _: None)
    assert len(connection.calls) == 10 and resumed["reused_partitions"] == 1
    assert old.read_bytes() == b"existing research data"


def test_changed_end_or_stock_scope_refetches_affected_partitions(tmp_path):
    args = list(arguments(tmp_path))
    download_bulk(Connection(), *args, progress=lambda _: None)
    args[4] = "2025-01-30"
    connection = Connection()
    report = download_bulk(connection, *args, progress=lambda _: None)
    assert report["reused_partitions"] == 1  # Static company data are unchanged.
    args[1] = ("000001",)
    connection = Connection()
    report = download_bulk(connection, *args, progress=lambda _: None)
    assert report["reused_partitions"] == 1  # Only benchmark codes are unchanged.
    assert len(connection.calls) == 10


@pytest.mark.parametrize("bad", ["out_of_range", "wrong_code", "duplicate", "too_many"])
def test_invalid_daily_partition_is_not_published(tmp_path, monkeypatch, bad):
    class BadConnection(Connection):
        def raw_sql(self, *args, **kwargs):
            frame = super().raw_sql(*args, **kwargs)
            if bad == "out_of_range":
                frame["trddt"] = date(2026, 1, 1)
            elif bad == "wrong_code":
                frame["stkcd"] = "600999"
            elif bad == "duplicate":
                frame = pd.concat([frame, frame], ignore_index=True)
            return frame

    if bad == "too_many":
        monkeypatch.setattr("quant_lab.data.csmar_bulk.MAX_PARTITION_ROWS", 1)
        monkeypatch.setattr(__name__ + ".MAX_PARTITION_ROWS", 1)
    with pytest.raises(ValueError):
        download_bulk(BadConnection(), *arguments(tmp_path), progress=lambda _: None)
    assert not list(tmp_path.rglob("*.parquet"))


def test_empty_tables_flagged_and_completed_means_only_extraction(tmp_path):
    class EmptyConnection(Connection):
        def raw_sql(self, *args, **kwargs):
            return super().raw_sql(*args, **kwargs).iloc[:0]

    report = download_bulk(EmptyConnection(), *arguments(tmp_path), progress=lambda _: None)
    assert len(report["empty_tables"]) == 11
    assert report["coverage"][BULK_TABLES[0].key]["missing_all_history_codes"] == [
        "000001", "600000",
    ]
    assert report["activated"] is False


def test_corrupt_cache_and_storage_budget_stop_before_query(tmp_path):
    args = list(arguments(tmp_path))
    download_bulk(Connection(), *args, progress=lambda _: None)
    parquet = args[-2] / "csmar_trade/trd_dalyr/2025-01.parquet"
    parquet.write_bytes(b"corrupt")
    connection = Connection()
    with pytest.raises(ValueError, match="checksum"):
        download_bulk(connection, *args, progress=lambda _: None)
    assert connection.calls == [] and parquet.read_bytes() == b"corrupt"
    args[-1] = StorageBudget(tmp_path, 1, 0)
    with pytest.raises(RuntimeError, match="budget"):
        download_bulk(connection, *args, progress=lambda _: None)
    assert connection.calls == []


def test_partial_membership_cache_is_never_used_as_full_stock_pool(tmp_path, monkeypatch):
    module = script()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    reference = tmp_path / "reference"
    reference.mkdir()
    pd.DataFrame({"symbol": ["000001.SZ"]}).to_parquet(reference / "historical_symbols.parquet")
    config = {"storage": {"reference_dir": "reference"}}
    assert module.load_completed_scope(config, "2025-01-31") is None


def test_download_lock_blocks_second_writer_and_is_released(tmp_path):
    module = script()
    lock = tmp_path / "bulk.lock"
    with (
        module.download_lock(lock), pytest.raises((RuntimeError, OSError)),
        module.download_lock(lock),
    ):
        pytest.fail("Second writer acquired the same lock")
    with module.download_lock(lock):
        pass
