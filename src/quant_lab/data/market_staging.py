"""Auditable raw-price consolidation; never activates incomplete research data."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from quant_lab.data.baostock_supplement import NUMERIC, expected_sessions, normalize_raw
from quant_lab.data.csmar_sample import _write_json
from quant_lab.data.storage_budget import StorageBudget, write_budgeted_frame

KEYS = ["trade_date", "symbol"]
MARKET = ["open", "high", "low", "close", "preclose", "volume", "amount"]
CSMAR_RENAME = {
    "trddt": "trade_date", "opnprc": "open", "hiprc": "high", "loprc": "low",
    "clsprc": "close", "precloseprice": "preclose", "dnshrtrd": "volume",
    "dnvaltrd": "amount", "limitup": "up_limit", "limitdown": "down_limit",
    "dsmvtll": "csmar_market_value_raw", "dsmvosd": "csmar_float_market_value_raw",
    "dretwd": "csmar_return_cash_reinvested", "dretnd": "csmar_return_ex_cash",
    "trdsta": "csmar_trading_status_raw",
}


def normalize_csmar(frame: pd.DataFrame, symbol_map: dict[str, str]) -> pd.DataFrame:
    result = frame.rename(columns=CSMAR_RENAME).copy()
    result["symbol"] = result["stkcd"].astype(str).str.zfill(6).map(symbol_map)
    result["trade_date"] = pd.to_datetime(result["trade_date"], errors="raise").astype("datetime64[ns]")
    if result[KEYS].isna().any().any() or result.duplicated(KEYS).any():
        raise ValueError("Unmapped CSMAR symbol, date or duplicate daily key")
    for column in CSMAR_RENAME.values():
        if column != "trade_date":
            result[column] = pd.to_numeric(result[column], errors="raise")
    return result[[*KEYS, *[c for c in CSMAR_RENAME.values() if c != "trade_date"]]]


def merge_raw_sources(csmar: pd.DataFrame, baostock: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Prefer each observed CSMAR field, preserving missing/status provenance."""
    for frame in (csmar, baostock):
        if frame.duplicated(KEYS).any():
            raise ValueError("Duplicate daily key before source merge")
    merged = csmar.merge(baostock, how="outer", on=KEYS, suffixes=("_cs", "_bs"),
                         validate="one_to_one", indicator=True)
    differences = {}
    source_columns = []
    for column in MARKET:
        cs, bs = merged[f"{column}_cs"], merged[f"{column}_bs"]
        overlap = cs.notna() & bs.notna()
        tolerance = 0.011 if column in MARKET[:5] else 1.0
        mismatch = overlap & (cs - bs).abs().gt(tolerance)
        differences[column] = {"overlap": int(overlap.sum()), "different": int(mismatch.sum()),
                               "different_over_one_part_per_million": int((mismatch & (cs - bs).abs().gt(cs.abs() * 1e-6)).sum()),
                               "max_absolute_difference": float((cs - bs).abs().max()) if overlap.any() else None}
        merged[column] = cs.combine_first(bs)
        source = f"{column}_source"
        merged[source] = np.select([cs.notna(), bs.notna()], ["csmar", "baostock"], default="missing")
        source_columns.append(source)
    merged["source_presence"] = merged.pop("_merge").astype(str)
    for column in ["is_st", "is_suspended", "is_st_known", "is_suspended_known"]:
        merged[column] = merged[column].eq(True)
    merged["status_source"] = np.where(
        merged["is_st_known"] & merged["is_suspended_known"], "baostock", "unknown"
    )
    # CSMAR supplies actual daily price-limit fields. BaoStock's reconstructed
    # historical rules have not yet been independently validated for this merge.
    merged["limit_status_known"] = merged["up_limit"].gt(0) & merged["down_limit"].gt(0)
    merged["price_adjustment"] = "raw"
    merged["research_ready"] = False
    extra_cs = [c for c in CSMAR_RENAME.values() if c not in {*KEYS, *MARKET}]
    extra_bs = [c for c in NUMERIC if c not in MARKET]
    columns = [*KEYS, *MARKET, *extra_bs, *extra_cs, "is_st", "is_st_known",
               "is_suspended", "is_suspended_known", "limit_status_known",
               *source_columns, "status_source", "source_presence", "price_adjustment", "research_ready"]
    return merged[columns].sort_values(KEYS).reset_index(drop=True), differences


def _register_baostock(connection, root: Path) -> None:
    columns = [*KEYS, *NUMERIC, "is_st", "is_st_known", "is_suspended", "is_suspended_known"]
    projections = ", ".join(f'"{c}"' for c in columns)
    pieces = []
    archive = sorted((root / "data/raw/baostock_consolidated").glob("*.parquet"))
    sources = [
        (1, "data/raw/baostock/daily"), (2, "data/raw/baostock_daily/raw_daily"),
        (4, "data/raw/baostock_supplement/raw_daily"),
    ]
    if archive:
        connection.read_parquet([str(p) for p in archive], union_by_name=True).create_view("bao_archive")
        pieces.append(f"SELECT {projections}, 2 AS priority FROM bao_archive")
        sources = [(4, "data/raw/baostock_supplement/raw_daily")]
    for priority, relative in sources:
        paths = [str(p) for p in sorted((root / relative).glob("*.parquet"))]
        if paths:
            name = f"bao_{priority}"
            connection.read_parquet(paths, union_by_name=True).create_view(name)
            pieces.append(f"SELECT {projections}, {priority} AS priority FROM {name}")
    crosses = [normalize_raw(pd.read_parquet(p)) for p in sorted(
        (root / "data/raw/baostock_supplement/cross_sections").glob("*.parquet")
    )]
    if crosses:
        connection.register("crosses", pd.concat(crosses, ignore_index=True))
        # The current-check workflow re-fetches the newest daily cross-section.
        # It must supersede an older per-stock cache for that same session.
        pieces.append(f"SELECT {projections}, 5 AS priority FROM crosses")
    if not pieces:
        raise ValueError("No local BaoStock raw observations")
    connection.execute("CREATE VIEW bao AS SELECT * EXCLUDE(priority) FROM (" +
                       " UNION ALL ".join(pieces) +
                       ") QUALIFY row_number() OVER(PARTITION BY trade_date,symbol ORDER BY priority DESC)=1")


def build_raw_staging(root: Path, start: str, end: str, budget: StorageBudget) -> dict:
    """Write yearly staging files and exact coverage gaps without active-path edits."""
    base = root / "data/processed/csmar_staging"
    reference = root / "data/raw/market_reference"
    symbols = sorted(pd.read_parquet(reference / "historical_symbols.parquet")["symbol"])
    symbol_map = {symbol.split(".")[0]: symbol for symbol in symbols}
    calendar = pd.read_parquet(reference / "trade_calendar.parquet")
    sessions = pd.DatetimeIndex(calendar.loc[calendar["is_trading_day"], "trade_date"])
    basics = pd.read_parquet(root / "data/raw/baostock_supplement/reference/stock_basic.parquet")
    basics["symbol"] = basics["code"].str[3:] + "." + basics["code"].str[:2].str.upper()
    basic_map = basics.set_index("symbol").to_dict("index")
    expected = {symbol: expected_sessions(sessions, start, end, basic_map.get(symbol)) for symbol in symbols}
    report = {"status": "building_raw_staging", "start": start, "end": end,
              "activated": False, "old_files_deleted": False, "partitions": [],
              "scope_stocks": len(symbols), "rows": 0, "close_from_csmar": 0,
              "close_from_baostock": 0, "invalid_market_rows": 0,
              "explicit_suspension_rows_without_prices": 0,
              "amount_outside_price_range_rows": 0,
              "missing_baostock_sessions": 0, "missing_merged_sessions": 0,
              "membership_complete": False, "ready_for_backtest": False}
    _write_json(base / "manifest.json", report, budget)
    gaps, comparisons = [], {}
    with duckdb.connect() as connection:
        connection.execute("SET memory_limit='1GB'")
        connection.execute("SET threads=2")
        _register_baostock(connection, root)
        for year in range(pd.Timestamp(start).year, pd.Timestamp(end).year + 1):
            first = max(pd.Timestamp(start), pd.Timestamp(year, 1, 1))
            last = min(pd.Timestamp(end), pd.Timestamp(year, 12, 31))
            bao = connection.execute("SELECT * FROM bao WHERE trade_date BETWEEN ? AND ?",
                                     [first, last]).df()
            bao = normalize_raw(bao.loc[bao["symbol"].isin(symbols)])
            cs_paths = sorted((root / "data/raw/csmar/bulk/csmar_trade/trd_dalyr").glob(f"{year}-*.parquet"))
            if not cs_paths:
                raise ValueError(f"Missing CSMAR download partitions for {year}")
            cs = normalize_csmar(pd.concat([pd.read_parquet(p) for p in cs_paths], ignore_index=True), symbol_map)
            cs = cs.loc[cs["trade_date"].between(first, last)]
            merged, differences = merge_raw_sources(cs, bao)
            if not merged["trade_date"].isin(sessions).all():
                raise ValueError("Non-trading date in raw market records")
            bao_dates = {s: pd.DatetimeIndex(g["trade_date"]) for s, g in bao.groupby("symbol")}
            merged_dates = {s: pd.DatetimeIndex(g["trade_date"]) for s, g in merged.groupby("symbol")}
            for symbol, all_expected in expected.items():
                days = all_expected[(all_expected >= first) & (all_expected <= last)]
                missing_bao = days.difference(bao_dates.get(symbol, pd.DatetimeIndex([])))
                missing_merged = days.difference(merged_dates.get(symbol, pd.DatetimeIndex([])))
                for kind, missing in [("baostock_status_and_raw", missing_bao), ("merged_raw", missing_merged)]:
                    if len(missing):
                        gaps.append({"symbol": symbol, "year": year, "kind": kind,
                                     "missing_sessions": len(missing), "first": str(missing.min().date()),
                                     "last": str(missing.max().date())})
                report["missing_baostock_sessions"] += len(missing_bao)
                report["missing_merged_sessions"] += len(missing_merged)
            missing_prices = merged[MARKET[:4]].isna().any(axis=1)
            explicit_suspension = merged["is_suspended"] & merged["is_suspended_known"]
            invalid = ((merged[MARKET[:4]] <= 0).any(axis=1)
                       | (missing_prices & ~explicit_suspension)
                       | (merged["high"] < merged[["open", "close", "low"]].max(axis=1))
                       | (merged["low"] > merged[["open", "close", "high"]].min(axis=1))
                       | (merged[["volume", "amount"]] < 0).any(axis=1))
            report["invalid_market_rows"] += int(invalid.sum())
            report["explicit_suspension_rows_without_prices"] += int((missing_prices & explicit_suspension).sum())
            average_price = merged["amount"].div(merged["volume"].where(merged["volume"].gt(0)))
            merged["amount_price_consistency_known"] = average_price.notna() & merged["low"].gt(0) & merged["high"].gt(0)
            merged["amount_outside_price_range"] = merged["amount_price_consistency_known"] & (
                average_price.lt(merged["low"] - 0.011) | average_price.gt(merged["high"] + 0.011)
            )
            report["amount_outside_price_range_rows"] += int(merged["amount_outside_price_range"].sum())
            report["close_from_csmar"] += int(merged["close_source"].eq("csmar").sum())
            report["close_from_baostock"] += int(merged["close_source"].eq("baostock").sum())
            path = base / "raw_daily" / f"{year}.parquet"
            write_budgeted_frame(merged, path, budget)
            report["rows"] += len(merged)
            report["partitions"].append({"year": year, "rows": len(merged), "bytes": path.stat().st_size,
                                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            comparisons[str(year)] = differences
            _write_json(base / "manifest.json", report, budget)
            print(f"Raw staging {year}: {len(merged):,} rows", flush=True)
    if gaps:
        write_budgeted_frame(pd.DataFrame(gaps), base / "coverage_gaps.parquet", budget)
    else:
        write_budgeted_frame(pd.DataFrame(columns=["symbol", "year", "kind", "missing_sessions", "first", "last"]),
                             base / "coverage_gaps.parquet", budget)
    _write_json(base / "cross_provider_comparison.json", comparisons, budget)
    report["status"] = "raw_staged_pending_research_acceptance"
    report["observed_session_coverage_passed"] = (
        report["missing_baostock_sessions"] == 0 and report["missing_merged_sessions"] == 0
    )
    report["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    report["parquet_bytes"] = sum(p["bytes"] for p in report["partitions"])
    report["gap_definition"] = (
        "Missing observed symbol/session keys between provider IPO and delisting dates. "
        "These are candidates for investigation, not proof the stock traded; code changes, "
        "suspensions and basic-information date issues must be resolved separately."
    )
    report["blocking_checks"] = ["common adjustment series", "historical member shortfalls",
                                  "BaoStock fallback price-limit rules", "market-cap units", "financial disclosure/revision semantics"]
    if not report["observed_session_coverage_passed"]:
        report["blocking_checks"].insert(0, "raw/status coverage")
    if report["invalid_market_rows"] or report["amount_outside_price_range_rows"]:
        report["blocking_checks"].insert(0, "flagged historical market-data anomalies")
    _write_json(base / "manifest.json", report, budget)
    return report
