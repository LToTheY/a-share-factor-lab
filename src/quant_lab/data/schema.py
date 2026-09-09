"""Canonical long-table contract for daily A-share research data."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
import pandas as pd

KEY_COLUMNS = ["trade_date", "symbol"]
PRICE_COLUMNS = ["open", "high", "low", "close"]
REQUIRED_COLUMNS = KEY_COLUMNS + PRICE_COLUMNS + ["volume", "amount"]
BOOLEAN_COLUMNS = ["is_st", "is_suspended", "is_limit_up", "is_limit_down"]
KNOWN_COLUMNS = ["is_st_known", "is_suspended_known", "limit_status_known"]


@dataclass(frozen=True)
class ValidationReport:
    rows: int
    symbols: int
    start_date: pd.Timestamp
    end_date: pd.Timestamp
    missing_ratio: float


def normalize_daily_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize types, defaults and sorting without silently dropping rows."""
    missing = sorted(set(REQUIRED_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    result = frame.copy()
    result["trade_date"] = pd.to_datetime(result["trade_date"]).dt.normalize()
    result["symbol"] = result["symbol"].astype(str)

    for column in PRICE_COLUMNS + ["volume", "amount", "adj_factor", "market_cap"]:
        if column in result:
            result[column] = pd.to_numeric(result[column], errors="coerce")

    for column in BOOLEAN_COLUMNS:
        if column not in result:
            result[column] = False
        result[column] = result[column].fillna(False).astype(bool)
    for column in KNOWN_COLUMNS:
        if column not in result:
            result[column] = False
        result[column] = result[column].fillna(False).astype(bool)

    if "adj_factor" not in result:
        result["adj_factor"] = 1.0
    if "list_date" in result:
        result["list_date"] = pd.to_datetime(result["list_date"]).dt.normalize()
    if "industry" not in result:
        result["industry"] = "UNKNOWN"

    return result.sort_values(KEY_COLUMNS).reset_index(drop=True)


def validate_daily_frame(frame: pd.DataFrame) -> ValidationReport:
    """Fail fast on errors that commonly create fake backtest performance."""
    normalized = normalize_daily_frame(frame)
    if normalized.duplicated(KEY_COLUMNS).any():
        samples = normalized.loc[
            normalized.duplicated(KEY_COLUMNS, keep=False), KEY_COLUMNS
        ].head(5)
        raise ValueError(f"Duplicate date-symbol keys:\n{samples}")

    if (normalized[PRICE_COLUMNS] <= 0).any().any():
        raise ValueError("OHLC prices must be strictly positive")
    if (normalized[["volume", "amount"]] < 0).any().any():
        raise ValueError("Volume and amount cannot be negative")
    if (normalized["high"] < normalized[["open", "close", "low"]].max(axis=1)).any():
        raise ValueError("High price is lower than another OHLC field")
    if (normalized["low"] > normalized[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("Low price is higher than another OHLC field")

    numeric = normalized.select_dtypes(include=[np.number])
    missing_ratio = float(numeric.isna().to_numpy().mean()) if numeric.size else 0.0
    return ValidationReport(
        rows=len(normalized),
        symbols=normalized["symbol"].nunique(),
        start_date=normalized["trade_date"].min(),
        end_date=normalized["trade_date"].max(),
        missing_ratio=missing_ratio,
    )


def require_columns(frame: pd.DataFrame, columns: Iterable[str]) -> None:
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise ValueError(f"Missing columns: {missing}")
