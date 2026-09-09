"""Turn provider partitions into a canonical panel without inventing trades."""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant_lab.data.schema import PRICE_COLUMNS, normalize_daily_frame


def fill_explicit_suspensions(frame: pd.DataFrame) -> pd.DataFrame:
    """Mark suspended sessions at previous close while keeping amount/volume zero.

    Only rows explicitly identified by the provider as suspended are filled. Other
    missing prices remain missing and will be caught by the audit.
    """
    result = normalize_daily_frame(frame)
    result = result.sort_values(["symbol", "trade_date"]).copy()
    prior_close = result.groupby("symbol", sort=False)["close"].ffill()
    suspended = result["is_suspended"] & result["close"].isna()
    for column in PRICE_COLUMNS:
        result.loc[suspended, column] = prior_close.loc[suspended]
    if "adj_close" in result:
        prior_adjusted = result.groupby("symbol", sort=False)["adj_close"].ffill()
        for column in ["adj_open", "adj_high", "adj_low", "adj_close"]:
            if column in result:
                result.loc[suspended, column] = prior_adjusted.loc[suspended]
    result.loc[suspended, ["volume", "amount"]] = 0.0
    for column in ["adj_factor", "market_cap", "float_market_cap", "industry"]:
        if column in result:
            result[column] = result.groupby("symbol", sort=False)[column].ffill()
    result["was_suspension_filled"] = suspended
    return result.sort_values(["trade_date", "symbol"]).reset_index(drop=True)


def derive_limit_flags(frame: pd.DataFrame, tolerance: float = 1e-6) -> pd.DataFrame:
    """Conservatively block next-open orders when open is at the daily limit."""
    result = frame.copy()
    known = result.get("limit_status_known", False)
    if "up_limit" in result:
        result["is_limit_up"] = known & (
            result["open"] >= result["up_limit"] * (1 - tolerance)
        )
    if "down_limit" in result:
        result["is_limit_down"] = known & (
            result["open"] <= result["down_limit"] * (1 + tolerance)
        )
    return result


def make_adjusted_market(frame: pd.DataFrame) -> pd.DataFrame:
    """Create continuous OHLC prices normalized to each symbol's latest factor.

    This is a research return approximation. A production ledger should model
    cash dividends and share changes explicitly.
    """
    result = frame.sort_values(["symbol", "trade_date"]).copy()
    if "adj_close" in result:
        for column in PRICE_COLUMNS:
            adjusted_column = f"adj_{column}"
            if adjusted_column in result:
                result[column] = result[adjusted_column]
        result["price_is_adjusted"] = True
        return result.sort_values(["trade_date", "symbol"]).reset_index(drop=True)
    if "adj_factor" not in result:
        result["adj_factor"] = 1.0
    latest = result.groupby("symbol", sort=False)["adj_factor"].transform("last")
    scale = result["adj_factor"] / latest.replace(0, np.nan)
    for column in PRICE_COLUMNS:
        result[column] = result[column] * scale
    result["price_is_adjusted"] = True
    return result.sort_values(["trade_date", "symbol"]).reset_index(drop=True)
