"""Point-in-time tradability filters for an A-share panel."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from quant_lab.data.schema import require_columns


@dataclass(frozen=True)
class UniverseConfig:
    min_listed_days: int = 120
    exclude_st: bool = True
    exclude_suspended: bool = True
    min_amount: float = 0.0
    require_known_status: bool = False
    require_index_membership: bool = False


def build_universe_mask(
    frame: pd.DataFrame,
    config: UniverseConfig | None = None,
) -> pd.Series:
    """Return a boolean mask using information observable on each date.

    ``min_listed_days`` is interpreted as observed trading rows, not calendar
    days. Buy/sell limit constraints belong in the execution engine, not here.
    """
    config = config or UniverseConfig()
    require_columns(frame, ["trade_date", "symbol", "amount"])
    ordered = frame.sort_values(["symbol", "trade_date"])
    trading_age = ordered.groupby("symbol", sort=False).cumcount() + 1
    mask = trading_age >= config.min_listed_days

    if config.exclude_st and "is_st" in ordered:
        mask &= ~ordered["is_st"].fillna(False)
    if config.exclude_suspended and "is_suspended" in ordered:
        mask &= ~ordered["is_suspended"].fillna(False)
    if config.min_amount > 0:
        mask &= ordered["amount"].fillna(0) >= config.min_amount
    if config.require_known_status:
        for column in ["is_st_known", "is_suspended_known", "limit_status_known"]:
            if column not in ordered:
                mask &= False
            else:
                mask &= ordered[column].fillna(False)
    if config.require_index_membership:
        if "in_index" not in ordered:
            raise ValueError("Historical index membership is required but missing")
        mask &= ordered["in_index"].fillna(False)

    result = pd.Series(False, index=frame.index, name="in_universe")
    result.loc[ordered.index] = mask.to_numpy()
    return result


def attach_index_membership(
    market: pd.DataFrame,
    weights: pd.DataFrame,
) -> pd.DataFrame:
    """Attach latest known constituent snapshot without using future snapshots.

    The weight table requires ``trade_date``, ``symbol`` and optionally ``weight``.
    For each market day, only the latest snapshot on or before that day is used.
    """
    require_columns(market, ["trade_date", "symbol"])
    require_columns(weights, ["trade_date", "symbol"])
    result = market.copy()
    result["trade_date"] = pd.to_datetime(result["trade_date"])
    snapshots = weights.copy()
    snapshots["trade_date"] = pd.to_datetime(snapshots["trade_date"])
    snapshot_dates = pd.DatetimeIndex(sorted(snapshots["trade_date"].unique()))
    result["in_index"] = False
    result["index_weight"] = 0.0
    if snapshot_dates.empty:
        return result

    for trade_date, positions in result.groupby("trade_date").groups.items():
        location = snapshot_dates.searchsorted(trade_date, side="right") - 1
        if location < 0:
            continue
        snapshot = snapshots[snapshots["trade_date"] == snapshot_dates[location]]
        members = set(snapshot["symbol"])
        idx = pd.Index(positions)
        result.loc[idx, "in_index"] = result.loc[idx, "symbol"].isin(members)
        if "weight" in snapshot:
            weight_map = snapshot.set_index("symbol")["weight"]
            result.loc[idx, "index_weight"] = (
                result.loc[idx, "symbol"].map(weight_map).fillna(0.0)
            )
    return result


def apply_universe(
    frame: pd.DataFrame,
    config: UniverseConfig | None = None,
) -> pd.DataFrame:
    result = frame.copy()
    result["in_universe"] = build_universe_mask(result, config)
    return result
