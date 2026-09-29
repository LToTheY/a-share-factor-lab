"""Bounded, resumable CSMAR samples for source validation, never production inputs."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from quant_lab.data.storage_budget import StorageBudget

DEFAULT_CODES = ("000001", "000066", "000333", "300750", "600000", "600519")
MAX_ROWS = 20_000


@dataclass(frozen=True)
class SampleTable:
    library: str
    table: str
    code_column: str
    date_column: str
    columns: tuple[str, ...]

    @property
    def key(self) -> str:
        return f"{self.library}.{self.table}"


# Names verified against the account catalog on 2026-09-29. Units, status codes,
# and integer disclosure-date encodings remain unverified: preserve raw values.
TABLES = (
    SampleTable("csmar_trade", "trd_dalyr", "stkcd", "trddt", (
        "stkcd", "trddt", "opnprc", "hiprc", "loprc", "clsprc", "dnshrtrd",
        "dnvaltrd", "dsmvosd", "dsmvtll", "dretwd", "dretnd", "adjprcwd",
        "adjprcnd", "markettype", "trdsta", "precloseprice", "limitdown",
        "limitup", "limitstatus",
    )),
    SampleTable("csmar_trade", "trd_bwardquotation", "symbol", "tradingdate", (
        "symbol", "tradingdate", "openprice", "highprice", "lowprice",
        "closeprice", "volume", "amount", "totalshare", "circulatedshare",
        "marketvalue", "circulatedmarketvalue", "turnoverrate1", "statecode",
        "filling", "shortname",
    )),
    SampleTable("csmar_trade", "trd_adjustfactor", "symbol", "tradingdate", (
        "symbol", "tradingdate", "fwardfactor", "bwardfactor",
        "cumulatefwardfactor", "cumulatebwardfactor",
    )),
    SampleTable("csmar_trade", "trd_cptl", "stkcd", "exdistdt", (
        "stkcd", "exdistdt", "annodt", "paydt", "disttyp", "amount", "roprc",
    )),
    SampleTable("csmar_financial", "fs_combas", "stkcd", "accper", (
        "stkcd", "accper", "typrep", "a001000000", "a002000000",
        "a003000000", "a003100000", "ifcorrect", "declaredate",
    )),
    SampleTable("csmar_financial", "fs_comins", "stkcd", "accper", (
        "stkcd", "accper", "typrep", "b001100000", "b002000000",
        "b002000101", "ifcorrect", "declaredate",
    )),
    SampleTable("csmar_financial", "fs_comscfd", "stkcd", "accper", (
        "stkcd", "accper", "typrep", "c001000000", "ifcorrect", "declaredate",
    )),
    SampleTable("csmar_financial", "iar_rept", "stkcd", "accper", (
        "stkcd", "accper", "reptyp", "annodt", "profita", "profitb",
    )),
    SampleTable("csmar_financial", "iar_forecdt", "stkcd", "accper", (
        "stkcd", "accper", "firforecdt", "firchangdt", "secchangdt",
        "thirchangdt", "actudt",
    )),
)


def sample_plan(
    catalog: dict[str, Any], codes: tuple[str, ...], start: str, end: str,
) -> dict[str, Any]:
    """Validate every projected field before connecting or downloading any rows."""
    if not 1 <= len(codes) <= 10 or len(set(codes)) != len(codes):
        raise ValueError("Sample requires 1 to 10 distinct stock codes")
    if any(re.fullmatch(r"[0-9]{6}", code) is None for code in codes):
        raise ValueError("Stock codes must contain exactly six digits")
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last or (last - first).days > 1096:
        raise ValueError("Sample date range must be ordered and at most three years")
    fields = {}
    for spec in TABLES:
        available = {
            column["name"]: column for column in catalog.get("libraries", {})
            .get(spec.library, {}).get("tables", {}).get(spec.table, [])
        }
        missing = set(spec.columns) - set(available)
        if missing:
            raise ValueError(f"Catalog missing {spec.key} fields: {sorted(missing)}")
        if available[spec.date_column]["type"] != "date":
            raise ValueError(f"Catalog date type unverified: {spec.key}")
        fields[spec.key] = [available[name] for name in spec.columns]
    request = {
        "version": 1, "codes": sorted(codes), "start": first.isoformat(),
        "end": last.isoformat(), "max_rows_per_table": MAX_ROWS,
        "tables": [asdict(spec) for spec in TABLES], "catalog_fields": fields,
    }
    fingerprint = hashlib.sha256(
        json.dumps(request, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return {**request, "request_id": fingerprint}


def sample_query(spec: SampleTable) -> str:
    """Only fixed, allowlisted identifiers; all requested values are bound."""
    if spec not in TABLES:
        raise ValueError("Table is not in the sample allowlist")
    columns = ", ".join(f'"{name}"' for name in spec.columns)
    return (
        f'SELECT {columns} FROM "{spec.library}"."{spec.table}" '
        f'WHERE "{spec.code_column}" IN %(codes)s '
        f'AND "{spec.date_column}" BETWEEN %(start)s AND %(end)s '
        f'ORDER BY "{spec.code_column}", "{spec.date_column}" LIMIT %(limit)s'
    )


def _write_bytes(path: Path, payload: bytes, budget: StorageBudget) -> None:
    if not path.resolve().is_relative_to(budget.root.resolve()):
        raise ValueError("Sample output must stay within the budgeted data directory")
    temporary = path.with_suffix(path.suffix + ".tmp")
    if not temporary.resolve().is_relative_to(budget.root.resolve()):
        raise ValueError("Temporary output must stay within the data directory")
    # Existing output and any earlier interrupted temporary copy already count.
    budget.check(additional_bytes=len(payload))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_bytes(payload)
    temporary.replace(path)


def _write_json(path: Path, value: dict[str, Any], budget: StorageBudget) -> None:
    _write_bytes(
        path, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"), budget,
    )


def _validated_frame(
    frame: pd.DataFrame, spec: SampleTable, plan: dict[str, Any],
) -> pd.Series:
    if len(frame) > MAX_ROWS:
        raise ValueError(f"Sample row limit exceeded: {spec.key}; no truncated file saved")
    if list(frame.columns) != list(spec.columns):
        raise ValueError(f"Unexpected result columns: {spec.key}")
    dates = pd.to_datetime(frame[spec.date_column], errors="raise")
    if not frame[spec.code_column].isin(plan["codes"]).all():
        raise ValueError(f"Unrequested stock in result: {spec.key}")
    if not dates.between(plan["start"], plan["end"]).all():
        raise ValueError(f"Missing or out-of-range date: {spec.key}")
    return dates


def download_sample(
    connection: Any, catalog: dict[str, Any], plan: dict[str, Any],
    destination: Path, budget: StorageBudget, progress: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Save original projected values plus provenance; leave research inputs alone.

    Financial report-period filtering is for extraction only. These rows are NOT
    point-in-time financial factors. Initial disclosures and revisions still need
    separate validation, including distinguishing corrections from initial dates.
    """
    expected = sample_plan(catalog, tuple(plan["codes"]), plan["start"], plan["end"])
    if expected != plan:
        raise ValueError("Sample plan changed; rebuild it from the account catalog")
    budget.check()
    directory = destination / plan["request_id"]
    if not directory.resolve().is_relative_to(budget.root.resolve()):
        raise ValueError("Sample output must stay within the budgeted data directory")
    manifest = {
        "provider": "csmar_via_wrds", "status": "in_progress",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "activated": False, "units_verified": False,
        "financial_point_in_time_verified": False, "request": plan, "results": {},
    }
    manifest_path = directory / "manifest.json"
    _write_json(manifest_path, manifest, budget)
    for spec in TABLES:
        output = directory / f"{spec.key}.parquet"
        metadata_path = directory / f"{spec.key}.json"
        if output.is_file() and metadata_path.is_file():
            record = json.loads(metadata_path.read_text(encoding="utf-8"))
            if (
                record.get("request_id") != plan["request_id"]
                or record.get("sha256") != hashlib.sha256(output.read_bytes()).hexdigest()
            ):
                raise ValueError(f"Cached sample failed integrity check: {spec.key}")
            manifest["results"][spec.key] = record
            progress(f"Reused {spec.key}: {record['rows']} rows")
        else:
            progress(f"Downloading {spec.key} ...")
            frame = connection.raw_sql(
                sample_query(spec), params={
                    "codes": tuple(plan["codes"]),
                    "start": date.fromisoformat(plan["start"]),
                    "end": date.fromisoformat(plan["end"]), "limit": MAX_ROWS + 1,
                }, chunksize=None, coerce_float=False,
            )
            dates = _validated_frame(frame, spec, plan)
            sink = pa.BufferOutputStream()
            pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), sink,
                           compression="zstd")
            payload = sink.getvalue().to_pybytes()
            record = {
                "request_id": plan["request_id"], "source": spec.key,
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                "file": output.name, "rows": len(frame), "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "codes_present": sorted(frame[spec.code_column].unique().tolist()),
                "missing_requested_codes": sorted(
                    set(plan["codes"]) - set(frame[spec.code_column])
                ),
                "min_date": None if frame.empty else dates.min().date().isoformat(),
                "max_date": None if frame.empty else dates.max().date().isoformat(),
                "date_column": spec.date_column,
                "duplicate_code_date_rows": int(frame.duplicated(
                    [spec.code_column, spec.date_column], keep=False
                ).sum()),
                "null_counts": {name: int(n) for name, n in frame.isna().sum().items()},
            }
            _write_bytes(output, payload, budget)
            _write_json(metadata_path, record, budget)
            manifest["results"][spec.key] = record
            progress(f"Saved {spec.key}: {len(frame)} rows; "
                     f"latest {record['max_date']}; {len(payload):,} bytes")
        _write_json(manifest_path, manifest, budget)
    manifest["status"] = "downloaded_pending_validation"
    manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["empty_tables"] = [
        key for key, record in manifest["results"].items() if not record["rows"]
    ]
    _write_json(manifest_path, manifest, budget)
    progress(f"Sample report: {manifest_path}")
    return manifest
