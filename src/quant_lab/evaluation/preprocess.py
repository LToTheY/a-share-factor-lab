"""Cross-sectional factor preprocessing without future leakage."""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant_lab.data.schema import require_columns


def winsorize_mad(values: pd.Series, n_mad: float = 5.0) -> pd.Series:
    """Clip a cross-section using median absolute deviation."""
    median = values.median()
    mad = (values - median).abs().median()
    if pd.isna(mad) or mad == 0:
        return values.copy()
    robust_sigma = 1.4826 * mad
    return values.clip(median - n_mad * robust_sigma, median + n_mad * robust_sigma)


def zscore(values: pd.Series) -> pd.Series:
    std = values.std(ddof=0)
    if pd.isna(std) or std == 0:
        return pd.Series(np.nan, index=values.index, dtype=float)
    return (values - values.mean()) / std


def neutralize_cross_section(
    group: pd.DataFrame,
    factor_col: str,
    size_col: str | None = "market_cap",
    industry_col: str | None = "industry",
) -> pd.Series:
    """Return OLS residuals against log-size and industry dummies."""
    valid = group[factor_col].notna()
    columns = [pd.Series(1.0, index=group.index, name="intercept")]

    if size_col and size_col in group:
        size = np.log(group[size_col].where(group[size_col] > 0))
        columns.append(size.rename("log_size"))
        valid &= size.notna()
    if industry_col and industry_col in group:
        dummies = pd.get_dummies(group[industry_col], prefix="industry", dtype=float)
        if dummies.shape[1] > 1:
            columns.append(
                dummies.iloc[:, 1:]
            )  # drop one category to avoid collinearity

    design = pd.concat(columns, axis=1)
    valid &= design.notna().all(axis=1)
    residual = pd.Series(np.nan, index=group.index, dtype=float)
    if valid.sum() <= design.shape[1]:
        return residual
    x = design.loc[valid].to_numpy(dtype=float)
    y = group.loc[valid, factor_col].to_numpy(dtype=float)
    coefficients, *_ = np.linalg.lstsq(x, y, rcond=None)
    residual.loc[valid] = y - x @ coefficients
    return residual


def preprocess_factor(
    frame: pd.DataFrame,
    factor_col: str = "factor",
    n_mad: float = 5.0,
    neutralize_size: bool = True,
    neutralize_industry: bool = True,
) -> pd.DataFrame:
    """Winsorize, z-score, optionally neutralize, then z-score each date."""
    require_columns(frame, ["trade_date", "symbol", factor_col])
    result = frame.copy()
    result["factor_winsor"] = result.groupby("trade_date")[factor_col].transform(
        lambda values: winsorize_mad(values, n_mad)
    )
    result["factor_z"] = result.groupby("trade_date")["factor_winsor"].transform(zscore)

    if neutralize_size or neutralize_industry:
        neutralized = []
        for _, group in result.groupby("trade_date", sort=False):
            values = neutralize_cross_section(
                group,
                "factor_z",
                size_col="market_cap" if neutralize_size else None,
                industry_col="industry" if neutralize_industry else None,
            )
            neutralized.append(values)
        combined = pd.concat(neutralized).sort_index()
        result["factor_processed"] = combined
        result["factor_processed"] = result.groupby("trade_date")[
            "factor_processed"
        ].transform(zscore)
    else:
        result["factor_processed"] = result["factor_z"]
    return result
