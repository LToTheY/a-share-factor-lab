"""One adjustment convention for CSMAR-priority raw prices and BaoStock gaps."""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant_lab.data.baostock_client import derive_open_limit_flags


def prepare_price_panel(raw: pd.DataFrame, sessions: pd.DatetimeIndex, basics: pd.DataFrame) -> pd.DataFrame:
    """Adjust within continuous observed segments; never bridge unknown sessions.

    The scale starts at 1 in each segment. Returns follow BaoStock's documented
    exchange-preclose method, not a cash-dividend-reinvestment ledger.
    """
    if raw.duplicated(["trade_date", "symbol"]).any():
        raise ValueError("Duplicate raw prices")
    basics = basics.copy()
    if "symbol" not in basics:
        basics["symbol"] = basics["code"].str[3:] + "." + basics["code"].str[:2].str.upper()
    listed = pd.to_datetime(basics.set_index("symbol")["ipoDate"], errors="coerce")
    pieces = []
    for symbol, group in raw.groupby("symbol", sort=True):
        group = group.sort_values("trade_date").copy()
        group["list_date"] = listed.get(symbol, pd.NaT)
        positions = sessions.get_indexer(pd.DatetimeIndex(group["trade_date"]))
        if (positions < 0).any():
            raise ValueError("Observed non-trading date")
        ipo = listed.get(symbol, pd.NaT)
        group["listing_age_sessions"] = (
            positions - sessions.searchsorted(ipo) if pd.notna(ipo) and ipo >= sessions.min()
            else (999_999 if pd.notna(ipo) else -1)
        )
        # Only explicitly suspended rows can use an earlier observed close.
        suspension = group["is_suspended"] & group["is_suspended_known"]
        missing = suspension & group["close"].isna()
        # A valid exchange preclose can change during suspension after a
        # corporate action. Use that mark before carrying an earlier close,
        # otherwise the adjustment chain invents a return on the ex-date.
        previous = group["close"].where(group["close"].gt(0)).combine_first(
            group["preclose"].where(suspension & group["preclose"].gt(0))
        ).ffill()
        filled = missing.copy()
        for column in ["open", "high", "low", "close", "preclose"]:
            needs_mark = suspension & (group[column].isna() | group[column].le(0))
            group.loc[needs_mark, column] = previous.loc[needs_mark]
            filled |= needs_mark
        for column in ["volume", "amount"]:
            needs_zero = suspension & group[column].isna()
            group.loc[needs_zero, column] = 0.0
            filled |= needs_zero
        group["was_suspension_filled"] = filled
        prior_close = group["close"].shift()
        multiplier = prior_close.div(group["preclose"])
        broken = pd.Series(np.r_[True, np.diff(positions) != 1], index=group.index)
        broken |= multiplier.isna() | multiplier.le(0) | ~np.isfinite(multiplier)
        group["research_segment"] = broken.cumsum().astype("int64")
        group["adj_factor"] = multiplier.where(~broken, 1.0).groupby(group["research_segment"]).cumprod()
        for column in ["open", "high", "low", "close", "preclose"]:
            group[f"adj_{column}"] = group[column] * group["adj_factor"]
        supplied = group[["up_limit", "down_limit"]].copy()
        derived = derive_open_limit_flags(group)
        has_supplied = supplied.gt(0).all(axis=1)
        for column in ["up_limit", "down_limit"]:
            derived.loc[has_supplied, column] = supplied.loc[has_supplied, column]
        plausible = ((derived["high"] <= derived["up_limit"] + 0.011)
                     & (derived["low"] >= derived["down_limit"] - 0.011))
        derived["limit_status_known"] = has_supplied | (
            derived["is_st_known"] & derived["preclose"].gt(0)
            & derived["listing_age_sessions"].ge(5) & plausible
        ) | (derived["is_no_limit_session"] & derived["listing_age_sessions"].between(0, 4))
        derived["is_limit_up"] = derived["limit_status_known"] & derived["open"].sub(derived["up_limit"]).abs().le(0.0051)
        derived["is_limit_down"] = derived["limit_status_known"] & derived["open"].sub(derived["down_limit"]).abs().le(0.0051)
        average = derived["amount"].div(derived["volume"].where(derived["volume"].gt(0)))
        derived["amount_outside_price_range"] = average.notna() & (
            average.lt(derived["low"] - 0.011) | average.gt(derived["high"] + 0.011)
        )
        derived["adjustment_method"] = "exchange_preclose_chain_within_continuous_segment"
        pieces.append(derived)
    if not pieces:
        raise ValueError("No market observations")
    return pd.concat(pieces, ignore_index=True).sort_values(["trade_date", "symbol"]).reset_index(drop=True)
