from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from quant_lab.data.csmar_sample import (
    MAX_ROWS,
    TABLES,
    SampleTable,
    download_sample,
    sample_plan,
    sample_query,
)
from quant_lab.data.storage_budget import StorageBudget


def catalog():
    libraries = {}
    for spec in TABLES:
        library = libraries.setdefault(spec.library, {"tables": {}})
        library["tables"][spec.table] = [
            {"name": name, "type": "date" if name == spec.date_column else "numeric",
             "comment": name} for name in spec.columns
        ]
    return {"libraries": libraries}


def frame_for(spec):
    values = {name: [Decimal("1234567890123.12")] for name in spec.columns}
    values[spec.code_column] = ["000001"]
    values[spec.date_column] = [date(2025, 6, 30)]
    if "declaredate" in values:
        values["declaredate"] = ["2026-03-31"]
    return pd.DataFrame(values)


class Connection:
    def __init__(self, fail_at=None):
        self.calls = []
        self.fail_at = fail_at

    def raw_sql(self, sql, params, chunksize, coerce_float):
        spec = next(spec for spec in TABLES if f'"{spec.table}"' in sql)
        self.calls.append(spec.key)
        assert 'SELECT *' not in sql
        assert "000001" not in sql
        assert "IN %(codes)s" in sql
        assert "BETWEEN %(start)s AND %(end)s" in sql
        assert "LIMIT %(limit)s" in sql
        assert params == {
            "codes": ("000001",), "start": date(2025, 1, 1),
            "end": date(2025, 12, 31), "limit": MAX_ROWS + 1,
        }
        assert chunksize is None and coerce_float is False
        if spec.key == self.fail_at:
            raise ConnectionError("simulated disconnect")
        return frame_for(spec)


def arguments(tmp_path):
    source = catalog()
    plan = sample_plan(source, ("000001",), "2025-01-01", "2025-12-31")
    return source, plan, tmp_path / "raw/csmar/sample", StorageBudget(tmp_path, 10**7, 0)


def test_plan_refuses_missing_fields_unbounded_requests_and_unknown_identifiers():
    source = catalog()
    source["libraries"]["csmar_trade"]["tables"]["trd_dalyr"].pop()
    with pytest.raises(ValueError, match="Catalog missing"):
        sample_plan(source, ("000001",), "2025-01-01", "2025-12-31")
    with pytest.raises(ValueError, match="three years"):
        sample_plan(catalog(), ("000001",), "2015-01-01", "2025-12-31")
    with pytest.raises(ValueError, match="six digits"):
        sample_plan(catalog(), ("000001');--",), "2025-01-01", "2025-12-31")
    with pytest.raises(ValueError, match="allowlist"):
        sample_query(SampleTable("evil", "all_data", "code", "date", ("code",)))


def test_download_is_bounded_preserves_raw_values_and_reuses_verified_cache(tmp_path):
    args = arguments(tmp_path)
    connection = Connection()
    report = download_sample(connection, *args, progress=lambda _: None)
    assert len(connection.calls) == len(TABLES)
    assert report["status"] == "downloaded_pending_validation"
    assert report["activated"] is False
    assert report["financial_point_in_time_verified"] is False
    assert not (tmp_path / "processed").exists()
    directory = args[2] / args[1]["request_id"]
    financial = pd.read_parquet(directory / "csmar_financial.fs_combas.parquet")
    assert financial["a001000000"].iloc[0] == Decimal("1234567890123.12")
    assert financial["declaredate"].iloc[0] == "2026-03-31"
    assert financial["accper"].iloc[0] == date(2025, 6, 30)
    repeated = download_sample(connection, *args, progress=lambda _: None)
    assert len(connection.calls) == len(TABLES)
    assert repeated["results"] == report["results"]


def test_interruption_retains_finished_tables_and_does_not_claim_completion(tmp_path):
    args = arguments(tmp_path)
    with pytest.raises(ConnectionError):
        download_sample(Connection(fail_at=TABLES[1].key), *args, progress=lambda _: None)
    directory = args[2] / args[1]["request_id"]
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "in_progress"
    assert list(manifest["results"]) == [TABLES[0].key]
    connection = Connection()
    download_sample(connection, *args, progress=lambda _: None)
    assert TABLES[0].key not in connection.calls
    assert len(connection.calls) == len(TABLES) - 1


@pytest.mark.parametrize("failure", ["overflow", "wrong_stock", "future", "null_date"])
def test_bad_or_truncated_result_never_becomes_a_parquet_file(tmp_path, failure):
    class BadConnection(Connection):
        def raw_sql(self, *args, **kwargs):
            frame = super().raw_sql(*args, **kwargs)
            if failure == "overflow":
                return pd.concat([frame] * (MAX_ROWS + 1), ignore_index=True)
            if failure == "wrong_stock":
                frame["stkcd"] = "600000"
            else:
                frame["trddt"] = None if failure == "null_date" else date(2026, 1, 1)
            return frame

    with pytest.raises(ValueError):
        download_sample(BadConnection(), *arguments(tmp_path), progress=lambda _: None)
    assert not list(tmp_path.rglob("*.parquet"))


def test_budget_rejects_download_before_query_and_output_cannot_escape(tmp_path):
    source, plan, destination, _ = arguments(tmp_path)
    connection = Connection()
    (tmp_path / "existing.data").write_bytes(b"already stored")
    with pytest.raises(RuntimeError, match="budget"):
        download_sample(connection, source, plan, destination, StorageBudget(tmp_path, 1, 0))
    with pytest.raises(ValueError, match="within"):
        download_sample(connection, source, plan, tmp_path.parent / "outside",
                        StorageBudget(tmp_path, 10**7, 0))
    assert connection.calls == []


def test_corrupt_cache_is_not_reused_or_overwritten(tmp_path):
    args = arguments(tmp_path)
    download_sample(Connection(), *args, progress=lambda _: None)
    path = args[2] / args[1]["request_id"] / f"{TABLES[0].key}.parquet"
    path.write_bytes(b"corrupted test fixture")
    connection = Connection()
    with pytest.raises(ValueError, match="integrity"):
        download_sample(connection, *args, progress=lambda _: None)
    assert connection.calls == []
    assert path.read_bytes() == b"corrupted test fixture"


def test_empty_results_are_explicitly_flagged_for_review(tmp_path):
    class EmptyConnection(Connection):
        def raw_sql(self, *args, **kwargs):
            return super().raw_sql(*args, **kwargs).iloc[:0]

    report = download_sample(EmptyConnection(), *arguments(tmp_path), progress=lambda _: None)
    assert report["empty_tables"] == [spec.key for spec in TABLES]
    assert all(item["max_date"] is None for item in report["results"].values())
