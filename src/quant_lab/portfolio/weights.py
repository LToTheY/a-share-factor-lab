"""Convert processed factor scores into dated target weights."""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant_lab.data.schema import require_columns


def rebalance_dates(
    dates: pd.Series,
    frequency: str = "W-FRI",
    next_trading_date: str | pd.Timestamp | None = None,
) -> pd.DatetimeIndex:
    """Choose the last observed signal date in each calendar period."""
    unique = pd.Series(pd.to_datetime(dates).unique()).sort_values()
    if frequency.upper() == "D":
        return pd.DatetimeIndex(unique.to_numpy())
    selected = unique.groupby(unique.dt.to_period(frequency)).max()
    if next_trading_date is not None and not unique.empty:
        latest = pd.Timestamp(unique.iloc[-1])
        following = pd.Timestamp(next_trading_date)
        if latest.to_period(frequency) == following.to_period(frequency):
            selected = selected[selected != latest]
    return pd.DatetimeIndex(selected.to_numpy())


def top_n_weights(
    signals: pd.DataFrame,
    signal_col: str = "factor_processed",
    top_n: int = 20,
    frequency: str = "W-FRI",
    max_weight: float = 0.10,
    next_trading_date: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Build equal-weight long-only targets on scheduled signal dates."""
    require_columns(signals, ["trade_date", "symbol", signal_col])
    if top_n <= 0 or not 0 < max_weight <= 1:
        raise ValueError("top_n and max_weight must be positive")
    selected_dates = rebalance_dates(
        signals["trade_date"], frequency, next_trading_date=next_trading_date
    )
    work = signals[signals["trade_date"].isin(selected_dates)].dropna(
        subset=[signal_col]
    )
    rows = []
    for trade_date, group in work.groupby("trade_date", sort=True):
        chosen = group.nlargest(top_n, signal_col).copy()
        if chosen.empty:
            continue
        equal_weight = 1.0 / len(chosen)
        if equal_weight > max_weight + 1e-12:
            # The remainder deliberately stays in cash; silently breaching the cap
            # would make the configuration misleading.
            weight = max_weight
        else:
            weight = equal_weight
        chosen["target_weight"] = weight
        rows.append(chosen[["trade_date", "symbol", "target_weight", signal_col]])
    if not rows:
        return pd.DataFrame(
            columns=["trade_date", "symbol", "target_weight", signal_col]
        )
    return pd.concat(rows, ignore_index=True)


def buffered_top_n_weights(
    signals: pd.DataFrame,
    signal_col: str = "factor_processed",
    top_n: int = 20,
    exit_rank: int = 30,
    frequency: str = "W-FRI",
    max_weight: float = 0.05,
    next_trading_date: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Build stable targets: enter near the top, exit only outside a rank buffer."""
    require_columns(signals, ["trade_date", "symbol", signal_col])
    if exit_rank < top_n or top_n <= 0:
        raise ValueError("Require exit_rank >= top_n > 0")
    selected_dates = rebalance_dates(
        signals["trade_date"], frequency, next_trading_date=next_trading_date
    )
    work = signals[signals["trade_date"].isin(selected_dates)].dropna(
        subset=[signal_col]
    )
    previous: list[str] = []
    rows = []
    for trade_date, group in work.groupby("trade_date", sort=True):
        ranked = group.sort_values(signal_col, ascending=False).copy()
        ranked["factor_rank"] = np.arange(1, len(ranked) + 1)
        rank_map = dict(zip(ranked["symbol"], ranked["factor_rank"]))
        kept = [
            symbol for symbol in previous if rank_map.get(symbol, np.inf) <= exit_rank
        ]
        for symbol in ranked["symbol"]:
            if len(kept) >= top_n:
                break
            if symbol not in kept:
                kept.append(symbol)
        previous = kept[:top_n]
        chosen = ranked[ranked["symbol"].isin(previous)].copy()
        chosen["target_weight"] = min(1.0 / len(previous), max_weight)
        rows.append(
            chosen[["trade_date", "symbol", "target_weight", signal_col, "factor_rank"]]
        )
    if not rows:
        return pd.DataFrame(
            columns=[
                "trade_date",
                "symbol",
                "target_weight",
                signal_col,
                "factor_rank",
            ]
        )
    return pd.concat(rows, ignore_index=True)
