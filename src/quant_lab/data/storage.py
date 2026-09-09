"""Storage helpers: Parquet in normal use, CSV fallback for minimal demos."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def write_table(frame: pd.DataFrame, path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    suffix = output.suffix.lower()
    if suffix == ".parquet":
        try:
            frame.to_parquet(output, index=False)
        except ImportError as exc:
            raise RuntimeError(
                "Parquet needs pyarrow. Install with: pip install pyarrow"
            ) from exc
    elif suffix == ".csv":
        frame.to_csv(output, index=False, encoding="utf-8-sig")
    else:
        raise ValueError("Only .parquet and .csv are supported")
    return output


def read_table(path: str | Path) -> pd.DataFrame:
    source = Path(path)
    if source.suffix.lower() == ".parquet":
        return pd.read_parquet(source)
    if source.suffix.lower() == ".csv":
        return pd.read_csv(source, parse_dates=["trade_date"])
    raise ValueError("Only .parquet and .csv are supported")
