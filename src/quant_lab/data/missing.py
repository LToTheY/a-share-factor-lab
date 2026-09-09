"""Classify missing market observations instead of blanket filling or dropping."""

from __future__ import annotations

import numpy as np
import pandas as pd


def classify_market_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach one auditable reason code to each daily market row."""
    result = frame.copy()
    raw_missing = result[["open", "high", "low", "close", "volume", "amount"]].isna().any(axis=1)
    adjusted_columns = [
        column
        for column in ["adj_open", "adj_high", "adj_low", "adj_close"]
        if column in result
    ]
    adjusted_missing = (
        result[adjusted_columns].isna().any(axis=1)
        if adjusted_columns
        else pd.Series(True, index=result.index)
    )
    suspended = result.get("is_suspended", False)
    if not isinstance(suspended, pd.Series):
        suspended = pd.Series(bool(suspended), index=result.index)
    reason = np.select(
        [
            suspended.astype(bool),
            raw_missing & ~suspended.astype(bool),
            adjusted_missing & ~suspended.astype(bool),
        ],
        ["SUSPENDED", "PROVIDER_GAP", "ADJUSTMENT_GAP"],
        default="OK",
    )
    result["data_quality_reason"] = reason
    result["is_usable_market_data"] = result["data_quality_reason"].isin(
        ["OK", "SUSPENDED"]
    )
    return result
