"""Shared historical index reference data without forward member backfills."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from quant_lab.data.daily_update import _with_retry
from quant_lab.data.storage import write_table
from quant_lab.data.storage_budget import StorageBudget

INDEX_SIZES = {"000300.SH": 300, "000905.SH": 500}
INDEX_LABELS = {"000300.SH": "hs300", "000905.SH": "zz500"}


def validate_snapshot(
    snapshot: pd.DataFrame, index_code: str, request_date: str | pd.Timestamp,
    *, allow_one_missing_for_download: bool = False,
) -> pd.DataFrame:
    if index_code not in INDEX_SIZES:
        raise ValueError(f"Unsupported index: {index_code}")
    if not {"trade_date", "symbol"}.issubset(snapshot.columns):
        raise ValueError("Snapshot is missing date/symbol columns")
    result = snapshot.copy()
    result["trade_date"] = pd.to_datetime(
        result["trade_date"], errors="raise"
    ).astype("datetime64[ns]")
    if result["trade_date"].isna().any() or result["symbol"].isna().any():
        raise ValueError("Null snapshot date or symbol")
    expected = INDEX_SIZES[index_code]
    allowed_sizes = {expected, expected - 1} if allow_one_missing_for_download else {expected}
    if len(result) not in allowed_sizes or result["symbol"].duplicated().any():
        raise ValueError(f"Incomplete or duplicate snapshot for {index_code}")
    if result["trade_date"].nunique() != 1:
        raise ValueError("A snapshot must contain exactly one effective date")
    if result["trade_date"].max() > pd.Timestamp(request_date).normalize():
        raise ValueError("Provider returned a future snapshot; historical use refused")
    if not result["symbol"].str.fullmatch(r"[0-9]{6}\.(SH|SZ)").all():
        raise ValueError("Invalid A-share symbol")
    return result.sort_values("symbol").reset_index(drop=True)


def collect_memberships(
    downloader: Any,
    calendar: pd.DataFrame,
    reference_dir: Path,
    legacy_dir: Path,
    indices: list[str],
    frequency: str = "W-FRI",
    budget: StorageBudget | None = None,
    *, allow_one_missing_for_download: bool = False,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Reuse verified legacy snapshots; download only absent request dates."""
    dates = pd.to_datetime(calendar.loc[calendar["is_trading_day"], "trade_date"])
    dates = dates.sort_values().drop_duplicates()
    if dates.empty:
        raise ValueError("No trading dates")
    # The first session avoids filling early dates with a later snapshot.
    requests = sorted(set(dates.groupby(dates.dt.to_period(frequency)).max()) | {
        pd.Timestamp(dates.iloc[0])
    })
    counts = {"downloaded_snapshots": 0, "reused_snapshots": 0,
              "incomplete_snapshot_requests": 0}
    frames = []
    effective_members: dict[tuple[str, pd.Timestamp], frozenset[str]] = {}
    for index_code in indices:
        label = INDEX_LABELS[index_code]
        for number, date in enumerate(requests, start=1):
            date_text = pd.Timestamp(date).strftime("%Y-%m-%d")
            name = f"{label}_request_{date_text}.parquet"
            destination = reference_dir / "membership_snapshots" / name
            legacy = legacy_dir / "membership_snapshots" / name
            source = destination if destination.exists() else legacy
            if source.exists():
                snapshot = validate_snapshot(
                    pd.read_parquet(source), index_code, date,
                    allow_one_missing_for_download=allow_one_missing_for_download,
                )
                if source != destination:
                    if budget is not None:
                        budget.check(additional_bytes=1_000_000)
                    write_table(snapshot, destination)
                counts["reused_snapshots"] += 1
            else:
                if budget is not None:
                    budget.check(additional_bytes=1_000_000)
                snapshot = validate_snapshot(
                    _with_retry(
                        downloader,
                        lambda code=index_code, day=date_text: downloader.index_snapshot(
                            code, day
                        ),
                        attempts=3,
                    ), index_code, date,
                    allow_one_missing_for_download=allow_one_missing_for_download,
                )
                write_table(snapshot, destination)
                counts["downloaded_snapshots"] += 1
            key = (index_code, pd.Timestamp(snapshot["trade_date"].iloc[0]))
            members = frozenset(snapshot["symbol"])
            if key in effective_members and effective_members[key] != members:
                raise ValueError("Conflicting member lists for the same effective date")
            effective_members[key] = members
            snapshot["index_code"] = index_code
            snapshot["source"] = "baostock"
            complete = len(snapshot) == INDEX_SIZES[index_code]
            snapshot["snapshot_complete"] = complete
            snapshot["membership_quality"] = (
                "periodic_provider_snapshot" if complete else "incomplete_provider_snapshot"
            )
            counts["incomplete_snapshot_requests"] += int(not complete)
            frames.append(snapshot)
            if number % 25 == 0 or number == len(requests):
                print(f"{label} membership: {number}/{len(requests)}", flush=True)
    return (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates(["index_code", "trade_date", "symbol"])
        .sort_values(["index_code", "trade_date", "symbol"])
        .reset_index(drop=True),
        counts,
    )


def attach_shared_memberships(
    market: pd.DataFrame, memberships: pd.DataFrame, *, allow_observed_subset: bool = False,
) -> pd.DataFrame:
    """Use only contemporaneously observed names; never invent a missing member.

    Strict mode rejects shortfalls. Explicit observed-subset research keeps the
    known names and exposes the completeness of each effective snapshot.
    """
    if not allow_observed_subset and "snapshot_complete" in memberships and not memberships["snapshot_complete"].all():
        raise ValueError(
            "Incomplete member snapshots may define download scope only; "
            "resolve their coverage before claiming a complete research universe"
        )
    result = market.copy()
    for code, label in INDEX_LABELS.items():
        subset = memberships.loc[memberships["index_code"].eq(code)].copy()
        if subset.empty:
            raise ValueError(f"Missing historical membership for {code}")
        subset["trade_date"] = pd.to_datetime(subset["trade_date"])
        dates = pd.DatetimeIndex(sorted(subset["trade_date"].unique()))
        positions = dates.searchsorted(pd.to_datetime(result["trade_date"]), side="right") - 1
        effective = pd.Series(pd.NaT, index=result.index, dtype="datetime64[ns]")
        observed = positions >= 0
        effective.loc[observed] = dates.take(positions[observed]).to_numpy()
        wanted = pd.MultiIndex.from_arrays([effective, result["symbol"]])
        known = pd.MultiIndex.from_frame(subset[["trade_date", "symbol"]])
        result[f"in_{label}"] = wanted.isin(known)
        complete = (subset.groupby("trade_date")["snapshot_complete"].all()
                    if "snapshot_complete" in subset else pd.Series(True, index=dates))
        result[f"{label}_membership_complete"] = effective.map(complete).fillna(False).astype(bool)
        result[f"{label}_membership_date"] = effective
    result["in_csi800"] = result["in_hs300"] | result["in_zz500"]
    return result
