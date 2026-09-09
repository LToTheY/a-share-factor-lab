"""Leakage-aware model table construction and chronological splitting."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import pandas as pd

from quant_lab.data.schema import require_columns


@dataclass
class TimeSplit:
    train: pd.DataFrame
    validation: pd.DataFrame
    test: pd.DataFrame


def combine_factor_tables(
    tables: Iterable[pd.DataFrame],
    names: Iterable[str],
) -> pd.DataFrame:
    """Outer-join canonical factor tables into a date-symbol feature matrix."""
    table_list = list(tables)
    name_list = list(names)
    if len(table_list) != len(name_list) or not table_list:
        raise ValueError("Provide one unique name per non-empty factor table")
    if len(set(name_list)) != len(name_list):
        raise ValueError("Feature names must be unique")

    combined = None
    for table, name in zip(table_list, name_list):
        require_columns(table, ["trade_date", "symbol", "factor"])
        current = table[["trade_date", "symbol", "factor"]].rename(
            columns={"factor": name}
        )
        combined = (
            current
            if combined is None
            else combined.merge(
                current, on=["trade_date", "symbol"], how="outer", validate="one_to_one"
            )
        )
    return combined.sort_values(["trade_date", "symbol"]).reset_index(drop=True)


def chronological_split(
    frame: pd.DataFrame,
    train_end: str,
    validation_end: str,
    embargo_days: int = 0,
) -> TimeSplit:
    """Split by date; optionally remove a calendar-day gap around boundaries."""
    require_columns(frame, ["trade_date"])
    dates = pd.to_datetime(frame["trade_date"])
    train_boundary = pd.Timestamp(train_end)
    validation_boundary = pd.Timestamp(validation_end)
    if train_boundary >= validation_boundary:
        raise ValueError("train_end must be earlier than validation_end")
    gap = pd.Timedelta(days=embargo_days)
    train = frame[dates <= train_boundary - gap].copy()
    validation = frame[
        (dates > train_boundary + gap) & (dates <= validation_boundary - gap)
    ].copy()
    test = frame[dates > validation_boundary + gap].copy()
    if train.empty or validation.empty or test.empty:
        raise ValueError("All chronological splits must contain observations")
    return TimeSplit(train=train, validation=validation, test=test)


def cross_sectional_rank_label(
    frame: pd.DataFrame,
    return_col: str,
    output_col: str = "label",
) -> pd.DataFrame:
    """Map each date's future return to approximately [-0.5, 0.5]."""
    require_columns(frame, ["trade_date", return_col])
    result = frame.copy()
    result[output_col] = (
        result.groupby("trade_date")[return_col].rank(pct=True, method="average") - 0.5
    )
    return result
