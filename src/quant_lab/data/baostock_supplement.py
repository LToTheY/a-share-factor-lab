"""Fill canonical raw-price/status gaps while retaining provider provenance."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from quant_lab.data.baostock_client import (
    DAILY_FIELDS,
    baostock_to_symbol,
    symbol_to_baostock,
)
from quant_lab.data.csmar_sample import _write_json
from quant_lab.data.daily_update import _with_retry
from quant_lab.data.storage_budget import StorageBudget, write_budgeted_frame

RAW_RENAME = {
    "date": "trade_date", "turn": "turnover_rate", "peTTM": "pe_ttm",
    "pbMRQ": "pb_mrq", "psTTM": "ps_ttm", "pcfNcfTTM": "pcf_ncf_ttm",
}
NUMERIC = (
    "open", "high", "low", "close", "preclose", "volume", "amount",
    "turnover_rate", "pe_ttm", "pb_mrq", "ps_ttm", "pcf_ncf_ttm",
)


def normalize_raw(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize BaoStock responses or existing canonical raw-price partitions."""
    result = frame.rename(columns=RAW_RENAME).copy()
    if "symbol" not in result:
        result["symbol"] = result["code"].map(baostock_to_symbol)
    if "adjustflag" in result and not result["adjustflag"].eq("3").all():
        raise ValueError("Raw-price supplement received adjusted prices")
    result["trade_date"] = pd.to_datetime(result["trade_date"], errors="raise").astype("datetime64[ns]")
    if result["trade_date"].isna().any() or result["symbol"].isna().any():
        raise ValueError("Null stock/date in BaoStock response")
    for column in NUMERIC:
        if column not in result:
            result[column] = np.nan
        result[column] = pd.to_numeric(result[column].replace("", np.nan), errors="raise")
    if "tradestatus" in result:
        result["is_suspended_known"] = result["tradestatus"].isin(["0", "1"])
        result["is_suspended"] = result["tradestatus"].eq("0")
    if "isST" in result:
        result["is_st_known"] = result["isST"].isin(["0", "1"])
        result["is_st"] = result["isST"].eq("1")
    statuses = ["is_st", "is_st_known", "is_suspended", "is_suspended_known"]
    for column in statuses:
        if column not in result:
            result[column] = False
        result[column] = result[column].fillna(False).astype(bool)
    result = result[["trade_date", "symbol", *NUMERIC, *statuses]]
    if result.duplicated(["trade_date", "symbol"]).any():
        raise ValueError("Duplicate stock/date in raw-price source")
    return result.sort_values(["trade_date", "symbol"]).reset_index(drop=True)


def expected_sessions(
    sessions: pd.DatetimeIndex, start: str, end: str, basic: dict | None,
) -> pd.DatetimeIndex:
    first, last = pd.Timestamp(start), pd.Timestamp(end)
    if basic:
        ipo = pd.to_datetime(basic.get("ipoDate"), errors="coerce")
        out = pd.to_datetime(basic.get("outDate"), errors="coerce")
        if pd.notna(ipo):
            first = max(first, ipo)
        if pd.notna(out):
            # outDate is the effective delisting date, not an eligible trade day.
            last = min(last, out - pd.Timedelta(days=1))
    return sessions[(sessions >= first) & (sessions <= last)]


def missing_ranges(
    expected: pd.DatetimeIndex, observed: pd.DatetimeIndex,
) -> list[tuple[str, str]]:
    """Find internal as well as edge gaps, grouped by consecutive market sessions."""
    mask = ~expected.isin(observed)
    locations = np.flatnonzero(mask)
    if not len(locations):
        return []
    groups = np.split(locations, np.flatnonzero(np.diff(locations) > 1) + 1)
    # A bounded full history per stock is small; cap fragmented network requests.
    if len(groups) > 8:
        groups = [locations]
    return [(str(expected[g[0]].date()), str(expected[g[-1]].date())) for g in groups]


def read_symbol_sources(
    root: Path, base: Path, symbol: str, cross_sections: pd.DataFrame,
) -> pd.DataFrame:
    filename = symbol.replace(".", "_") + ".parquet"
    frames = []
    # The older small cache has known unique raw-price observations (000066.SZ).
    archive = sorted((root / "data/raw/baostock_consolidated").glob("*.parquet"))
    if archive:
        with duckdb.connect() as connection:
            connection.read_parquet([str(p) for p in archive]).create_view("archive")
            frames.append(normalize_raw(connection.execute("SELECT * FROM archive WHERE symbol = ?", [symbol]).df()))
    for relative in ([] if archive else ["data/raw/baostock/daily", "data/raw/baostock_daily/raw_daily"]):
        path = root / relative / filename
        if path.is_file():
            frames.append(normalize_raw(pd.read_parquet(path)))
    current = base / "raw_daily" / filename
    if current.is_file():
        frames.append(normalize_raw(pd.read_parquet(current)))
    if not cross_sections.empty:
        frames.append(cross_sections.loc[cross_sections["symbol"].eq(symbol)])
    if not frames:
        return pd.DataFrame(columns=["trade_date", "symbol", *NUMERIC,
                                     "is_st", "is_st_known", "is_suspended", "is_suspended_known"])
    return pd.concat(frames, ignore_index=True).drop_duplicates(
        ["trade_date", "symbol"], keep="last"
    ).sort_values("trade_date").reset_index(drop=True)


def refresh_benchmarks(client, base: Path, sessions: pd.DatetimeIndex, start, end,
                       budget: StorageBudget, *, refresh_latest: bool = False) -> dict:
    """Keep both comparison indices current instead of only creating them once."""
    expected = sessions[(sessions >= pd.Timestamp(start)) & (sessions <= pd.Timestamp(end))]
    if expected.empty:
        raise ValueError("No benchmark sessions requested")
    result = {}
    fields = "date,code,open,high,low,close,preclose,volume,amount,pctChg"
    for code in ["sh.000300", "sh.000905"]:
        path = base / "benchmarks" / f"{code}.parquet"
        cached = pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=fields.split(","))
        observed = pd.DatetimeIndex(pd.to_datetime(cached["date"]))
        ranges = missing_ranges(expected, observed)
        if refresh_latest and expected[-1] in observed:
            ranges.append((str(expected[-1].date()), str(expected[-1].date())))
        incoming = []
        for first, last in ranges:
            frame = _with_retry(client, lambda code=code, first=first, last=last: client._to_frame(
                client.bs.query_history_k_data_plus(code, fields, start_date=first, end_date=last,
                                                    frequency="d", adjustflag="3"), "benchmark"), attempts=3)
            dates = pd.to_datetime(frame["date"])
            if frame.empty or not frame["code"].eq(code).all() or not dates.between(first, last).all():
                raise ValueError(f"Incomplete or unrequested benchmark response: {code}")
            incoming.append(frame)
        combined = pd.concat([cached, *incoming], ignore_index=True)
        combined["date"] = pd.to_datetime(combined["date"]).dt.strftime("%Y-%m-%d")
        combined = combined.drop_duplicates(["date", "code"], keep="last").sort_values("date")
        missing = expected.difference(pd.DatetimeIndex(pd.to_datetime(combined["date"])))
        required = combined.loc[pd.to_datetime(combined["date"]).isin(expected)]
        prices = required[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
        if len(missing) or prices.isna().any(axis=None) or not np.isfinite(prices).all(axis=None) or prices.le(0).any(axis=None):
            raise ValueError(f"Benchmark has missing or invalid sessions: {code}")
        if incoming:
            write_budgeted_frame(combined, path, budget)
        result[code] = {"data_through": str(expected[-1].date()), "rows": len(combined),
                        "downloaded_rows": sum(len(frame) for frame in incoming)}
    return result


def fill_gaps(
    client: Any, root: Path, start: str, end: str, budget: StorageBudget,
    *, progress: Callable[[str], None] = print,
) -> dict[str, Any]:
    base = root / "data/raw/baostock_supplement"
    reference = root / "data/raw/market_reference"
    symbols = sorted(pd.read_parquet(reference / "historical_symbols.parquet")["symbol"])
    calendar = pd.read_parquet(reference / "trade_calendar.parquet")
    sessions = pd.DatetimeIndex(calendar.loc[calendar["is_trading_day"], "trade_date"]).astype("datetime64[ns]")
    if sessions.max() < pd.Timestamp(end):
        raise ValueError("Reference calendar must cover requested end")
    basic_path = base / "reference/stock_basic.parquet"
    if basic_path.is_file():
        basics = pd.read_parquet(basic_path)
    else:
        basics = _with_retry(client, lambda: client._to_frame(
            client.bs.query_stock_basic(), "stock_basic"), attempts=3)
        write_budgeted_frame(basics, basic_path, budget)
    basics["symbol"] = basics["code"].map(baostock_to_symbol)
    if basics["symbol"].duplicated().any():
        raise ValueError("Duplicate stock-basic entries need review")
    basic_by_symbol = basics.set_index("symbol").to_dict("index")
    report: dict[str, Any] = {
        "status": "in_progress", "start": start, "end": end,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope_stocks": len(symbols), "completed_stocks": 0,
        "downloaded_rows": 0, "requests": 0, "failures": [], "unresolved": [],
        "raw_sources": [], "activated": False, "old_files_deleted": False,
    }
    manifest = base / "manifest.json"
    _write_json(manifest, report, budget)
    consecutive_failures = 0
    # One daily request replaces hundreds of per-stock requests for the common
    # recent suffix. Keep only the requested historical pool on disk.
    import pyarrow.parquet as pq

    legacy_path = root / "data/processed/baostock_daily.parquet"
    archive = sorted((root / "data/raw/baostock_consolidated").glob("*.parquet"))
    if archive:
        with duckdb.connect() as connection:
            connection.read_parquet([str(p) for p in archive]).create_view("archive")
            counts = connection.execute("SELECT trade_date, count(*) AS n FROM archive GROUP BY trade_date ORDER BY trade_date").df()
        recent_counts = counts.set_index("trade_date")["n"].tail(20)
        last_existing = pd.Timestamp(counts["trade_date"].max())
    else:
        legacy_dates = pq.read_table(legacy_path, columns=["trade_date"]).column("trade_date").to_pandas()
        last_existing = pd.Timestamp(legacy_dates.max())
        recent_counts = legacy_dates.value_counts().sort_index().tail(20)
    thin_dates = recent_counts[recent_counts < recent_counts.max() * 0.9].index
    common_days = sessions[((sessions > last_existing) | sessions.isin(thin_dates)) & (sessions <= pd.Timestamp(end))]
    for day in common_days:
        path = base / "cross_sections" / f"{day.date()}.parquet"
        if path.is_file():
            continue
        progress(f"Daily supplement: {day.date()}")
        try:
            frame = _with_retry(client, lambda day=day: client._to_frame(
                client.bs.query_daily_history_k_AStock(str(day.date())), "daily_cross_section"
            ), attempts=3)
            frame = normalize_raw(frame)
            frame = frame.loc[frame["symbol"].isin(symbols)].copy()
            if frame.empty or not frame["trade_date"].eq(day).all():
                raise ValueError("Daily response is empty or has the wrong date")
            write_budgeted_frame(frame, path, budget)
            report["requests"] += 1
            report["downloaded_rows"] += len(frame)
            consecutive_failures = 0
        except Exception as exc:  # noqa: BLE001 - record vendor failures for resumption
            report["failures"].append({"day": str(day.date()), "error_type": type(exc).__name__})
            consecutive_failures += 1
        if consecutive_failures >= 3:
            report["status"] = "provider_unavailable_resume_required"
            _write_json(manifest, report, budget)
            return report
        _write_json(manifest, report, budget)
    crosses = [normalize_raw(pd.read_parquet(path)) for path in sorted(
        (base / "cross_sections").glob("*.parquet")
    )]
    cross = pd.concat(crosses, ignore_index=True) if crosses else pd.DataFrame()
    for number, symbol in enumerate(symbols, start=1):
        existing = read_symbol_sources(root, base, symbol, cross)
        expected = expected_sessions(sessions, start, end, basic_by_symbol.get(symbol))
        observed = pd.DatetimeIndex(pd.to_datetime(existing["trade_date"]))
        ranges = missing_ranges(expected, observed)
        incoming = []
        for first, last in ranges:
            progress(f"[{number}/{len(symbols)}] {symbol}: {first} to {last}")
            try:
                frame = _with_retry(client, lambda first=first, last=last, symbol=symbol: client._to_frame(
                    client.bs.query_history_k_data_plus(
                        symbol_to_baostock(symbol), DAILY_FIELDS, start_date=first,
                        end_date=last, frequency="d", adjustflag="3",
                    ), "raw_history"
                ), attempts=3)
                report["requests"] += 1
                consecutive_failures = 0
                if not frame.empty:
                    frame = normalize_raw(frame)
                    if not frame["symbol"].eq(symbol).all() or not frame["trade_date"].between(first, last).all():
                        raise ValueError("Unrequested stock/date in history")
                    incoming.append(frame)
                    report["downloaded_rows"] += len(frame)
                time.sleep(0.2)
            except Exception as exc:  # noqa: BLE001 - record vendor failures for resumption
                report["failures"].append({
                    "symbol": symbol, "start": first, "end": last,
                    "error_type": type(exc).__name__,
                })
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    break
        path = base / "raw_daily" / (symbol.replace(".", "_") + ".parquet")
        if incoming:
            cached = [normalize_raw(pd.read_parquet(path))] if path.is_file() else []
            saved = pd.concat([*cached, *incoming], ignore_index=True).drop_duplicates(
                ["trade_date", "symbol"], keep="last"
            ).sort_values("trade_date")
            write_budgeted_frame(saved, path, budget)
            existing = pd.concat([existing, *incoming], ignore_index=True).drop_duplicates(
                ["trade_date", "symbol"], keep="last"
            )
        missing = expected.difference(pd.DatetimeIndex(pd.to_datetime(existing["trade_date"])))
        if len(missing):
            report["unresolved"].append({
                "symbol": symbol, "missing_sessions": len(missing),
                "first": str(missing.min().date()), "last": str(missing.max().date()),
                "basic_available": symbol in basic_by_symbol,
            })
        report["completed_stocks"] = number
        report["raw_sources"].append({"symbol": symbol, "observed_rows": len(existing),
                                      "expected_sessions": len(expected), "missing_sessions": len(missing)})
        if consecutive_failures >= 3:
            report["status"] = "provider_unavailable_resume_required"
            _write_json(manifest, report, budget)
            return report
        if number % 25 == 0 or incoming or number == len(symbols):
            _write_json(manifest, report, budget)
            progress(f"Coverage: {number}/{len(symbols)}, new rows={report['downloaded_rows']:,}, "
                     f"unresolved stocks={len(report['unresolved'])}")
    report["benchmarks"] = refresh_benchmarks(client, base, sessions, start, end, budget)
    report["status"] = "raw_gaps_filled" if not report["unresolved"] and not report["failures"] else "gaps_remain"
    report["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    _write_json(manifest, report, budget)
    return report
