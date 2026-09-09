"""Time-local factor diagnostics for non-stationary markets."""

from __future__ import annotations

import pandas as pd

from quant_lab.evaluation.diagnostics import information_coefficient


def factor_stability_table(
    scores: pd.DataFrame,
    factor_names: list[str],
    label_col: str,
    recent_windows: list[int] | tuple[int, ...],
) -> pd.DataFrame:
    """Summarize each oriented factor by year and trailing trading-day windows."""
    rows: list[dict[str, object]] = []
    ordered_dates = pd.DatetimeIndex(sorted(scores["trade_date"].unique()))
    for factor in factor_names:
        ic = information_coefficient(scores, factor, label_col)
        annual = ic.dropna().groupby(ic.dropna().index.year)
        for year, values in annual:
            rows.append(
                {
                    "factor": factor,
                    "window": f"year_{year}",
                    "start_date": values.index.min(),
                    "end_date": values.index.max(),
                    "mean_ic": float(values.mean()),
                    "win_rate": float((values > 0).mean()),
                    "observations": int(values.count()),
                }
            )
        for length in recent_windows:
            dates = ordered_dates[-int(length) :]
            values = ic[ic.index.isin(dates)].dropna()
            rows.append(
                {
                    "factor": factor,
                    "window": f"recent_{int(length)}d",
                    "start_date": dates.min() if len(dates) else pd.NaT,
                    "end_date": dates.max() if len(dates) else pd.NaT,
                    "mean_ic": float(values.mean()) if not values.empty else float("nan"),
                    "win_rate": float((values > 0).mean())
                    if not values.empty
                    else float("nan"),
                    "observations": int(values.count()),
                }
            )
    return pd.DataFrame(rows)
