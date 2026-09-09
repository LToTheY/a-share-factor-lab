"""Resumable BaoStock update with raw execution and adjusted research prices."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import pandas as pd

from quant_lab.data.baostock_client import BaoStockDownloader, symbol_to_baostock
from quant_lab.data.storage import write_table

T = TypeVar("T")


def _with_retry(downloader: Any, operation: Callable[[], T], attempts: int = 5) -> T:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:  # noqa: BLE001 - external provider surface is broad
            last_error = exc
            if attempt < attempts and hasattr(downloader, "reconnect"):
                time.sleep(attempt)
                try:
                    downloader.reconnect()
                except Exception as reconnect_exc:  # noqa: BLE001 - provider login
                    last_error = reconnect_exc
    assert last_error is not None
    raise last_error


def _partition_start(
    existing: pd.DataFrame, requested_end: pd.Timestamp, initial_days: int
) -> pd.Timestamp:
    if existing.empty:
        return requested_end - pd.Timedelta(days=initial_days)
    return pd.Timestamp(existing["trade_date"].max()) + pd.Timedelta(days=1)


def _append_partition(
    downloader: BaoStockDownloader,
    symbol: str,
    path: Path,
    requested_end: pd.Timestamp,
    initial_days: int,
    adjustflag: str,
) -> int:
    existing = pd.read_parquet(path) if path.exists() else pd.DataFrame()
    start = _partition_start(existing, requested_end, initial_days)
    if start > requested_end:
        return 0
    incoming = _with_retry(
        downloader,
        lambda: downloader.daily_history(
            symbol_to_baostock(symbol),
            start.strftime("%Y-%m-%d"),
            requested_end.strftime("%Y-%m-%d"),
            adjustflag=adjustflag,
            include_adjustment_factors=False,
        ),
    )
    if incoming.empty:
        return 0
    combined = pd.concat([existing, incoming], ignore_index=True)
    combined = (
        combined.drop_duplicates(["trade_date", "symbol"], keep="last")
        .sort_values(["trade_date", "symbol"])
        .reset_index(drop=True)
    )
    write_table(combined, path)
    return len(incoming)


def incremental_zz500_update(
    downloader: BaoStockDownloader,
    output_dir: str | Path,
    end_date: str | None = None,
    initial_calendar_days: int = 800,
) -> dict[str, Any]:
    """Append missing raw and post-adjusted bars for the current CSI 500."""
    output = Path(output_dir)
    adjusted_dir = output / "daily"
    raw_dir = output / "raw_daily"
    snapshot_dir = output / "membership_snapshots"
    for directory in [adjusted_dir, raw_dir, snapshot_dir]:
        directory.mkdir(parents=True, exist_ok=True)

    requested_end = pd.Timestamp(end_date or pd.Timestamp.now().date()).normalize()
    saved_snapshots = sorted(snapshot_dir.glob("*.parquet"))
    try:
        snapshot = _with_retry(downloader, downloader.zz500_snapshot, attempts=2)
    except Exception:
        if not saved_snapshots:
            raise
        snapshot = pd.read_parquet(saved_snapshots[-1])
        print(
            f"WARNING: using cached membership snapshot {saved_snapshots[-1].name}",
            flush=True,
        )
    if snapshot.empty:
        raise RuntimeError("BaoStock returned an empty current CSI 500 snapshot")
    effective_date = pd.Timestamp(snapshot["trade_date"].max())
    write_table(snapshot, snapshot_dir / f"zz500_{effective_date.date()}.parquet")

    calendar_start = requested_end - pd.Timedelta(days=initial_calendar_days)
    calendar_end = requested_end + pd.Timedelta(days=14)
    calendar = _with_retry(
        downloader,
        lambda: downloader.trade_calendar(
            calendar_start.strftime("%Y-%m-%d"), calendar_end.strftime("%Y-%m-%d")
        ),
        attempts=5,
    )
    write_table(calendar, output / "trade_calendar.parquet")

    symbols = sorted(snapshot["symbol"].unique())
    failures: list[dict[str, str]] = []
    updated_adjusted = 0
    updated_raw = 0
    new_adjusted_rows = 0
    new_raw_rows = 0
    for number, symbol in enumerate(symbols, start=1):
        for kind, directory, flag in [
            ("adjusted", adjusted_dir, "1"),
            ("raw", raw_dir, "3"),
        ]:
            path = directory / f"{symbol.replace('.', '_')}.parquet"
            try:
                rows = _append_partition(
                    downloader,
                    symbol,
                    path,
                    requested_end,
                    initial_calendar_days,
                    flag,
                )
                if rows:
                    if kind == "adjusted":
                        updated_adjusted += 1
                        new_adjusted_rows += rows
                    else:
                        updated_raw += 1
                        new_raw_rows += rows
            except Exception as exc:  # noqa: BLE001 - isolate each cached partition
                failures.append({"symbol": symbol, "dataset": kind, "error": str(exc)})
        if number % 25 == 0 or number == len(symbols):
            print(
                f"Daily update: {number}/{len(symbols)}; "
                f"adjusted={updated_adjusted}/{new_adjusted_rows} rows, "
                f"raw={updated_raw}/{new_raw_rows} rows, failures={len(failures)}",
                flush=True,
            )

    adjusted_paths = [
        adjusted_dir / f"{symbol.replace('.', '_')}.parquet" for symbol in symbols
    ]
    raw_paths = [raw_dir / f"{symbol.replace('.', '_')}.parquet" for symbol in symbols]
    paired = [
        (left, right)
        for left, right in zip(adjusted_paths, raw_paths)
        if left.exists() and right.exists()
    ]
    latest_dates = [
        min(
            pd.Timestamp(
                pd.read_parquet(left, columns=["trade_date"])["trade_date"].max()
            ),
            pd.Timestamp(
                pd.read_parquet(right, columns=["trade_date"])["trade_date"].max()
            ),
        )
        for left, right in paired
    ]
    complete_through = min(latest_dates) if len(latest_dates) == len(symbols) else None
    requested_end_coverage = sum(date >= requested_end for date in latest_dates)
    open_dates = calendar.loc[calendar["is_trading_day"], "trade_date"].sort_values()
    expected_dates = open_dates[open_dates <= requested_end]
    expected_latest = (
        pd.Timestamp(expected_dates.max()) if not expected_dates.empty else None
    )
    if complete_through is None:
        stale_trading_days = None
        next_trade_date = None
    else:
        stale_trading_days = int(
            ((open_dates > complete_through) & (open_dates <= requested_end)).sum()
        )
        future = open_dates[open_dates > complete_through]
        next_trade_date = pd.Timestamp(future.iloc[0]) if not future.empty else None

    manifest = {
        "provider": "baostock",
        "mode": "incremental_current_zz500_dual_price",
        "requested_end": str(requested_end.date()),
        "expected_latest_trade_date": (
            str(expected_latest.date()) if expected_latest is not None else None
        ),
        "membership_effective_date": str(effective_date.date()),
        "initial_calendar_days": initial_calendar_days,
        "symbols": len(symbols),
        "cached_adjusted_symbols": sum(path.exists() for path in adjusted_paths),
        "cached_raw_symbols": sum(path.exists() for path in raw_paths),
        "complete_through": (
            str(complete_through.date()) if complete_through is not None else None
        ),
        "next_trade_date": (
            str(next_trade_date.date()) if next_trade_date is not None else None
        ),
        "stale_trading_days": stale_trading_days,
        "requested_end_coverage": requested_end_coverage,
        "updated_adjusted_symbols": updated_adjusted,
        "updated_raw_symbols": updated_raw,
        "new_adjusted_rows": new_adjusted_rows,
        "new_raw_rows": new_raw_rows,
        "failures": failures,
        "survivorship_warning": (
            "Dates before the first locally saved CSI 500 snapshot use current-member "
            "backfill. Point-in-time membership is used from the first snapshot onward."
        ),
    }
    (output / "update_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def _membership_panel(snapshot_files: list[Path]) -> pd.DataFrame:
    snapshots = pd.concat(
        [pd.read_parquet(path) for path in snapshot_files], ignore_index=True
    )
    return (
        snapshots.drop_duplicates(["trade_date", "symbol"], keep="last")
        .sort_values(["trade_date", "symbol"])
        .reset_index(drop=True)
    )


def consolidate_incremental_panel(
    output_dir: str | Path, end_date: str | pd.Timestamp | None = None
) -> pd.DataFrame:
    """Merge raw execution prices, adjusted research prices and PIT memberships."""
    output = Path(output_dir)
    snapshot_files = sorted((output / "membership_snapshots").glob("*.parquet"))
    if not snapshot_files:
        raise RuntimeError("No membership snapshot exists; run update first")
    memberships = _membership_panel(snapshot_files)
    symbols = sorted(memberships["symbol"].unique())
    frames = []
    for symbol in symbols:
        filename = f"{symbol.replace('.', '_')}.parquet"
        raw_path = output / "raw_daily" / filename
        adjusted_path = output / "daily" / filename
        if not raw_path.exists() or not adjusted_path.exists():
            continue
        raw = pd.read_parquet(raw_path)
        adjusted = pd.read_parquet(adjusted_path)[
            ["trade_date", "symbol", "open", "high", "low", "close", "preclose"]
        ].rename(
            columns={
                "open": "adj_open",
                "high": "adj_high",
                "low": "adj_low",
                "close": "adj_close",
                "preclose": "adj_preclose",
            }
        )
        frames.append(
            raw.merge(
                adjusted,
                on=["trade_date", "symbol"],
                how="inner",
                validate="one_to_one",
            )
        )
    if not frames:
        raise RuntimeError("No paired raw/adjusted daily histories exist")
    market = pd.concat(frames, ignore_index=True)
    if end_date is not None:
        market = market.loc[market["trade_date"] <= pd.Timestamp(end_date)].copy()
    market["adj_factor"] = market["adj_close"].div(market["close"])

    snapshot_dates = pd.DatetimeIndex(sorted(memberships["trade_date"].unique()))
    earliest = snapshot_dates[0]
    market["in_index"] = False
    market["membership_quality"] = "point_in_time_snapshot"
    for trade_date, positions in market.groupby("trade_date").groups.items():
        location = (
            snapshot_dates.searchsorted(pd.Timestamp(trade_date), side="right") - 1
        )
        if location < 0:
            location = 0
            market.loc[positions, "membership_quality"] = "current_member_backfill"
        members = set(
            memberships.loc[
                memberships["trade_date"] == snapshot_dates[location], "symbol"
            ]
        )
        market.loc[positions, "in_index"] = market.loc[positions, "symbol"].isin(
            members
        )
    market.attrs["provider"] = "baostock"
    market.attrs["universe"] = "zz500_local_point_in_time_snapshots"
    market.attrs["membership_backfill_before"] = str(earliest.date())
    market.attrs["research_warning"] = (
        "Dates before the first local CSI 500 snapshot use current-member backfill; "
        "later dates use the latest snapshot known on each date."
    )
    return market.sort_values(["trade_date", "symbol"]).reset_index(drop=True)
