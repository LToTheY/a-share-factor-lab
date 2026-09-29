"""Download bounded CSMAR partitions for the complete historical research pool.

Raw extraction only: neither an empty result nor a successful SQL query proves
coverage, unit correctness or point-in-time financial availability. Never switch
research inputs or delete legacy market data from this module.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from quant_lab.data.csmar_sample import TABLES, SampleTable, _write_bytes, _write_json
from quant_lab.data.storage_budget import StorageBudget

COMPANIES = SampleTable("csmar_trade", "trd_co", "stkcd", "listdt", (
    "stkcd", "stknme", "conme", "listdt", "markettype", "statco", "statdt",
    "indcd", "indnme", "nindcd", "nindnme", "nnindcd", "nnindnme",
))
BENCHMARKS = SampleTable("csmar_trade", "trd_index", "indexcd", "trddt", (
    "indexcd", "trddt", "opnindex", "hiindex", "loindex", "clsindex", "retindex",
))
BULK_TABLES = (*TABLES, COMPANIES, BENCHMARKS)
MONTHLY_TABLES = {spec.key for spec in TABLES[:3]}
MAX_PARTITION_ROWS = 150_000
MAX_STOCKS = 5_000


def validate_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    """Check allowlisted identifiers and documented date types before logging in."""
    fields = {}
    for spec in BULK_TABLES:
        available = {
            item["name"]: item for item in catalog.get("libraries", {})
            .get(spec.library, {}).get("tables", {}).get(spec.table, [])
        }
        missing = set(spec.columns) - set(available)
        if missing:
            raise ValueError(f"Catalog missing {spec.key} fields: {sorted(missing)}")
        if available[spec.date_column]["type"] != "date":
            raise ValueError(f"Unverified date type in {spec.key}")
        fields[spec.key] = [available[column] for column in spec.columns]
    return fields


def validate_codes(codes: tuple[str, ...]) -> None:
    if not codes or len(codes) > MAX_STOCKS or len(set(codes)) != len(codes):
        raise ValueError("Expected a nonempty distinct historical stock pool <= 5000 stocks")
    if any(len(code) != 6 or not code.isascii() or not code.isdigit() for code in codes):
        raise ValueError("Codes must be six ASCII digits")


def partition_windows(start: str, end: str, monthly: bool) -> Iterator[tuple[str, str]]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last:
        raise ValueError("History start must not follow end")
    cursor = first
    while cursor <= last:
        if monthly:
            boundary = date(cursor.year + cursor.month // 12, cursor.month % 12 + 1, 1)
        else:
            boundary = date(cursor.year + 1, 1, 1)
        stop = min(last, (pd.Timestamp(boundary) - pd.Timedelta(days=1)).date())
        yield cursor.isoformat(), stop.isoformat()
        cursor = boundary


def build_partitions(
    codes: tuple[str, ...], indices: tuple[str, ...], start: str, end: str,
) -> list[dict[str, Any]]:
    validate_codes(codes)
    validate_codes(indices)
    if not set(indices).issubset({"000300", "000905"}):
        raise ValueError("Benchmark scope must stay within HS300 and CSI500")
    if date.fromisoformat(start) > date.fromisoformat(end):
        raise ValueError("History start must not follow end")
    parts = []
    for spec in BULK_TABLES:
        static = spec == COMPANIES
        windows = [(None, None)] if static else partition_windows(
            start, end, monthly=spec.key in MONTHLY_TABLES
        )
        for first, last in windows:
            request = {
                "version": 1, "table": asdict(spec),
                "codes": sorted(indices if spec == BENCHMARKS else codes),
                "start": first, "end": last, "max_rows": MAX_PARTITION_ROWS,
            }
            signature = hashlib.sha256(
                json.dumps(request, sort_keys=True).encode("utf-8")
            ).hexdigest()
            label = "current" if static else (
                first[:7] if spec.key in MONTHLY_TABLES else first[:4]
            )
            parts.append({
                "request": request, "signature": signature,
                "relative_file": f"{spec.library}/{spec.table}/{label}.parquet",
            })
    return parts


def query_for(spec: SampleTable) -> str:
    if spec not in BULK_TABLES:
        raise ValueError("Table is outside the bulk allowlist")
    columns = ", ".join(f'"{column}"' for column in spec.columns)
    sql = (f'SELECT {columns} FROM "{spec.library}"."{spec.table}" '
           f'WHERE "{spec.code_column}" IN %(codes)s ')
    if spec != COMPANIES:
        sql += f'AND "{spec.date_column}" BETWEEN %(start)s AND %(end)s '
    return sql + f'ORDER BY "{spec.code_column}", "{spec.date_column}" LIMIT %(limit)s'


def _validate_frame(frame: pd.DataFrame, spec: SampleTable, request: dict) -> pd.Series:
    if len(frame) > MAX_PARTITION_ROWS:
        raise ValueError(f"Partition exceeds row limit: {spec.key}; refusing truncation")
    if list(frame.columns) != list(spec.columns):
        raise ValueError(f"Unexpected columns in {spec.key}")
    if not frame[spec.code_column].isin(request["codes"]).all():
        raise ValueError(f"Unrequested code in {spec.key}")
    dates = pd.to_datetime(frame[spec.date_column], errors="raise")
    if spec != COMPANIES and not dates.between(request["start"], request["end"]).all():
        raise ValueError(f"Invalid or out-of-range date in {spec.key}")
    if (spec.key in MONTHLY_TABLES or spec == BENCHMARKS) and frame.duplicated(
        [spec.code_column, spec.date_column]
    ).any():
        raise ValueError(f"Duplicate daily keys in {spec.key}")
    return dates


def _one_partition(
    connection: Any, spec: SampleTable, part: dict, destination: Path,
    budget: StorageBudget, refresh: bool,
) -> tuple[dict[str, Any], bool]:
    output = destination / part["relative_file"]
    sidecar = output.with_suffix(".json")
    if not refresh and output.is_file() and sidecar.is_file():
        cached = json.loads(sidecar.read_text(encoding="utf-8"))
        if cached.get("signature") == part["signature"]:
            if cached.get("sha256") != hashlib.sha256(output.read_bytes()).hexdigest():
                raise ValueError(f"Cached partition failed checksum: {part['relative_file']}")
            return cached, True
    request = part["request"]
    params = {"codes": tuple(request["codes"]), "limit": MAX_PARTITION_ROWS + 1}
    if spec != COMPANIES:
        params.update(start=date.fromisoformat(request["start"]),
                      end=date.fromisoformat(request["end"]))
    budget.check(additional_bytes=16_000_000)
    frame = connection.raw_sql(query_for(spec), params=params, chunksize=None,
                               coerce_float=False)
    dates = _validate_frame(frame, spec, request)
    sink = pa.BufferOutputStream()
    pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), sink,
                   compression="zstd")
    payload = sink.getvalue().to_pybytes()
    observed = sorted(frame[spec.code_column].unique().tolist())
    record = {
        **part, "source": spec.key, "rows": len(frame), "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "codes_present": observed,
        "missing_requested_codes": sorted(set(request["codes"]) - set(observed)),
        "min_date": None if dates.dropna().empty else dates.min().date().isoformat(),
        "max_date": None if dates.dropna().empty else dates.max().date().isoformat(),
        "null_counts": {column: int(n) for column, n in frame.isna().sum().items()},
    }
    _write_bytes(output, payload, budget)
    _write_json(sidecar, record, budget)
    return record, False


def download_bulk(
    connection: Any, catalog: dict[str, Any], codes: tuple[str, ...],
    indices: tuple[str, ...], start: str, end: str, destination: Path,
    budget: StorageBudget, *, refresh: bool = False,
    universe_reference: dict[str, Any] | None = None,
    progress: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Fetch the entire scoped pool in one authenticated session, with resumable files."""
    fields = validate_catalog(catalog)
    parts = build_partitions(codes, indices, start, end)
    if not destination.resolve().is_relative_to(budget.root.resolve()):
        raise ValueError("Bulk output must remain within the budgeted data directory")
    budget.check()
    table_by_key = {spec.key: spec for spec in BULK_TABLES}
    report: dict[str, Any] = {
        "status": "in_progress", "provider": "csmar_via_wrds",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "start": start, "end": end, "codes": sorted(codes), "indices": sorted(indices),
        "expected_partitions": len(parts), "completed_partitions": 0,
        "reused_partitions": 0, "parquet_bytes": 0, "rows": 0,
        "activated": False, "legacy_market_data_deleted": False,
        "baostock_market_data_downloaded": False,
        "units_verified": False, "financial_point_in_time_verified": False,
        "catalog_fields": fields, "partitions": [], "coverage": {},
        "universe_reference": universe_reference,
        "limitations": [
            "Historical membership is periodic, not exact change-event history.",
            "CSMAR subscription updates may lag the requested end date.",
            "Empty stock-periods can reflect listing history, vendor lag or missing data.",
            "Financial report dates and correction dates are not initial availability dates.",
            "Company-table industry labels are current metadata, not historical industries.",
            "BaoStock gap filling, normalization and activation are separate later steps.",
        ],
    }
    path = destination / "manifest.json"
    _write_json(path, report, budget)
    code_coverage: dict[str, set[str]] = {spec.key: set() for spec in BULK_TABLES}
    for number, part in enumerate(parts, start=1):
        info = part["request"]["table"]
        spec = table_by_key[f"{info['library']}.{info['table']}"]
        report["current_partition"] = part["relative_file"]
        _write_json(path, report, budget)
        progress(f"[{number}/{len(parts)}] {part['relative_file']} ...")
        try:
            record, reused = _one_partition(
                connection, spec, part, destination, budget, refresh,
            )
        except (Exception, KeyboardInterrupt) as exc:
            report["status"] = "interrupted"
            report["error_type"] = type(exc).__name__
            # Never persist the raw error, connection URL, username or credentials.
            try:
                _write_json(path, report, budget)
            except (OSError, RuntimeError):
                pass
            raise
        code_coverage[spec.key].update(record["codes_present"])
        coverage = report["coverage"].setdefault(spec.key, {
            "rows": 0, "min_date": None, "max_date": None, "empty_partitions": 0,
        })
        coverage["rows"] += record["rows"]
        coverage["empty_partitions"] += int(record["rows"] == 0)
        for field, selector in [("min_date", min), ("max_date", max)]:
            known = [value for value in [coverage[field], record[field]] if value]
            coverage[field] = selector(known) if known else None
        expected = indices if spec == BENCHMARKS else codes
        coverage["observed_codes"] = len(code_coverage[spec.key])
        coverage["missing_all_history_codes"] = sorted(set(expected) - code_coverage[spec.key])
        report["completed_partitions"] += 1
        report["reused_partitions"] += int(reused)
        report["parquet_bytes"] += record["bytes"]
        report["rows"] += record["rows"]
        report["partitions"].append({
            key: record[key] for key in
            ("relative_file", "signature", "sha256", "rows", "bytes", "min_date", "max_date")
        })
        _write_json(path, report, budget)
        progress(f"  {'Reused' if reused else 'Saved'} {record['rows']:,} rows, "
                 f"latest {record['max_date']}, "
                 f"total {report['parquet_bytes'] / 1e9:.3f} GB")
    report["status"] = "downloaded_pending_normalization_and_gap_review"
    report["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    report.pop("current_partition", None)
    report["empty_tables"] = [key for key, value in report["coverage"].items()
                              if not value["rows"]]
    _write_json(path, report, budget)
    progress(f"Bulk download report: {path}")
    return report
