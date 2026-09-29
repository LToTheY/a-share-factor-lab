"""IC, forward returns, quantile portfolios and factor summaries."""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant_lab.data.schema import require_columns


def add_forward_returns(
    frame: pd.DataFrame,
    periods: int = 5,
    price_col: str = "close",
) -> pd.DataFrame:
    """Add close(t+periods)/close(t)-1 within each symbol."""
    require_columns(frame, ["trade_date", "symbol", price_col])
    if periods <= 0:
        raise ValueError("periods must be positive")
    result = frame.sort_values(["symbol", "trade_date"]).copy()
    groups = ["symbol", "research_segment"] if "research_segment" in result else ["symbol"]
    future = result.groupby(groups, sort=False)[price_col].shift(-periods)
    result[f"forward_return_{periods}d"] = future / result[price_col] - 1.0
    return result.sort_values(["trade_date", "symbol"]).reset_index(drop=True)


def add_execution_returns(frame: pd.DataFrame, periods: int = 5) -> pd.DataFrame:
    """Next open to open h sessions later: a tradable-timing research label.

    This is a frictionless label, not a claim that each order can fill. Segment
    boundaries and missing adjusted opens stay missing rather than bridged.
    """
    require_columns(frame, ["trade_date", "symbol", "adj_open"])
    if not isinstance(periods, int) or periods <= 0:
        raise ValueError("periods must be a positive integer")
    work = frame.sort_values(["symbol", "trade_date"]).copy()
    groups = ["symbol", "research_segment"] if "research_segment" in work else ["symbol"]
    prices = work.groupby(groups, sort=False)["adj_open"]
    entry, exit_price = prices.shift(-1), prices.shift(-(periods + 1))
    work[f"next_open_return_{periods}d"] = (exit_price / entry - 1).where(entry.gt(0) & exit_price.gt(0))
    return work.sort_values(["trade_date", "symbol"]).reset_index(drop=True)


def hac_mean_summary(values: pd.Series, lags: int = 4) -> dict[str, float | int]:
    """Bartlett/Newey-West mean uncertainty, preserving missing session slots.

    Normal-approximation intervals are descriptive and unadjusted for testing
    many factors. Lags must cover at least the label-overlap horizon minus one.
    """
    if not isinstance(lags, int) or lags < 0:
        raise ValueError("lags must be a nonnegative integer")
    array = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    valid = np.isfinite(array)
    count = int(valid.sum())
    output = {"hac_mean": np.nan, "hac_se": np.nan, "hac_t": np.nan,
              "hac_ci_low": np.nan, "hac_ci_high": np.nan, "hac_lags": lags, "hac_observations": count}
    if count < 2:
        return output
    mean = float(array[valid].mean())
    residuals = np.where(valid, array - mean, 0.)
    lag_count = min(lags, len(array) - 1)
    variance_sum = float(residuals @ residuals)
    for lag in range(1, lag_count + 1):
        variance_sum += 2 * (1 - lag / (lag_count + 1)) * float(residuals[lag:] @ residuals[:-lag])
    se = float(np.sqrt(max(0., variance_sum) / count**2 * count / (count - 1)))
    output.update(hac_mean=mean, hac_se=se, hac_t=mean / se if se > 0 else np.nan,
                  hac_ci_low=mean - 1.96*se, hac_ci_high=mean + 1.96*se, hac_lags=lag_count)
    return output


def information_coefficient(
    frame: pd.DataFrame,
    factor_col: str,
    return_col: str,
    method: str = "spearman",
    min_observations: int = 10,
) -> pd.Series:
    """Compute one cross-sectional correlation per signal date."""
    require_columns(frame, ["trade_date", factor_col, return_col])

    def correlation(group: pd.DataFrame) -> float:
        valid = group[[factor_col, return_col]].dropna()
        valid = valid.replace([np.inf, -np.inf], np.nan).dropna()
        if len(valid) < min_observations or valid[factor_col].nunique() < 2 or valid[return_col].nunique() < 2:
            return np.nan
        left = valid[factor_col]
        right = valid[return_col]
        if method == "spearman":
            # Ranking first avoids pandas' optional SciPy dependency.
            left = left.rank(method="average")
            right = right.rank(method="average")
        elif method != "pearson":
            raise ValueError("method must be 'spearman' or 'pearson'")
        return float(left.corr(right, method="pearson"))

    result = frame.groupby("trade_date", sort=True).apply(
        correlation, include_groups=False
    )
    result.name = "rank_ic" if method == "spearman" else "ic"
    return result


def quantile_returns(
    frame: pd.DataFrame,
    factor_col: str,
    return_col: str,
    groups: int = 5,
) -> pd.DataFrame:
    """Equal-weight future return by daily factor quantile."""
    require_columns(frame, ["trade_date", "symbol", factor_col, return_col])
    if groups < 2:
        raise ValueError("groups must be at least 2")
    work = frame[["trade_date", "symbol", factor_col, return_col]].copy()
    work[[factor_col, return_col]] = work[[factor_col, return_col]].replace([np.inf, -np.inf], np.nan)
    # Membership is fixed using the signal-day score. Missing future labels may
    # lower evaluation coverage, but must never move other stocks between groups.
    work = work.dropna(subset=["trade_date", "symbol", factor_col])
    work = work.sort_values(["trade_date", "symbol"], kind="stable")

    def assign(values: pd.Series) -> pd.Series:
        ranks = values.rank(method="first")
        if len(ranks) < groups or values.nunique() < 2:
            return pd.Series(np.nan, index=values.index)
        return pd.qcut(ranks, groups, labels=False) + 1

    work["quantile"] = work.groupby("trade_date")[factor_col].transform(assign)
    result = (
        work.dropna(subset=["quantile"])
        .groupby(["trade_date", "quantile"], as_index=False)[return_col]
        .mean()
    )
    counts = work.dropna(subset=["quantile"]).groupby(["trade_date", "quantile"])[return_col].agg(
        assigned_count="size", observed_count="count").reset_index()
    result = result.merge(counts, on=["trade_date", "quantile"], validate="one_to_one")
    result["label_coverage"] = result["observed_count"] / result["assigned_count"]
    result["quantile"] = result["quantile"].astype(int)
    return result


def summarize_ic(ic: pd.Series, periods_per_year: float = 252 / 5) -> dict[str, float]:
    clean = ic.dropna()
    if clean.empty:
        return {"mean_ic": np.nan, "ic_std": np.nan, "icir": np.nan, "win_rate": np.nan}
    std = clean.std(ddof=1)
    return {
        "mean_ic": float(clean.mean()),
        "ic_std": float(std),
        "icir": float(clean.mean() / std * np.sqrt(periods_per_year))
        if std
        else np.nan,
        "win_rate": float((clean > 0).mean()),
    }


def ic_decay(
    frame: pd.DataFrame,
    factor_col: str,
    horizons: tuple[int, ...] = (1, 5, 10, 20),
    price_col: str = "close",
    min_observations: int = 10,
) -> pd.DataFrame:
    """Calculate mean RankIC at multiple forward horizons."""
    rows = []
    for horizon in horizons:
        labeled = add_forward_returns(frame, horizon, price_col)
        return_col = f"forward_return_{horizon}d"
        ic = information_coefficient(
            labeled, factor_col, return_col, min_observations=min_observations
        )
        summary = summarize_ic(ic, periods_per_year=252 / horizon)
        rows.append({"horizon": horizon, **summary, "observations": int(ic.count())})
    return pd.DataFrame(rows)


def annual_ic_summary(ic: pd.Series) -> pd.DataFrame:
    """Summarize IC by calendar year without annualizing within-year means."""
    work = ic.dropna().rename("ic").rename_axis("trade_date").reset_index()
    if work.empty:
        return pd.DataFrame(columns=["year", "mean_ic", "ic_std", "win_rate", "days"])
    work["year"] = pd.to_datetime(work["trade_date"]).dt.year
    return (
        work.groupby("year")["ic"]
        .agg(
            mean_ic="mean",
            ic_std="std",
            win_rate=lambda values: (values > 0).mean(),
            days="size",
        )
        .reset_index()
    )


def factor_rank_autocorrelation(
    frame: pd.DataFrame,
    factor_col: str,
    min_observations: int = 10,
) -> pd.Series:
    """Daily correlation between current and previous cross-sectional ranks."""
    require_columns(frame, ["trade_date", "symbol", factor_col])
    work = frame[["trade_date", "symbol", factor_col]].copy()
    work["rank"] = work.groupby("trade_date")[factor_col].rank(pct=True)
    work = work.sort_values(["symbol", "trade_date"])
    work["previous_rank"] = work.groupby("symbol", sort=False)["rank"].shift()

    def correlation(group: pd.DataFrame) -> float:
        valid = group[["rank", "previous_rank"]].dropna()
        if len(valid) < min_observations:
            return np.nan
        return float(valid["rank"].corr(valid["previous_rank"]))

    result = work.groupby("trade_date").apply(correlation, include_groups=False)
    result.name = "rank_autocorrelation"
    return result


def top_group_turnover(
    frame: pd.DataFrame,
    factor_col: str,
    top_fraction: float = 0.2,
    frequency: str = "W-FRI",
) -> pd.Series:
    """One-way constituent turnover of the top factor group at rebalance dates."""
    if not 0 < top_fraction < 1:
        raise ValueError("top_fraction must be between 0 and 1")
    dates = pd.Series(pd.to_datetime(frame["trade_date"]).unique()).sort_values()
    selected_dates = dates.groupby(dates.dt.to_period(frequency)).max()
    work = frame[frame["trade_date"].isin(selected_dates)].dropna(subset=[factor_col])
    previous: set[str] | None = None
    rows = {}
    for trade_date, group in work.groupby("trade_date", sort=True):
        count = max(1, int(np.ceil(len(group) * top_fraction)))
        current = set(group.nlargest(count, factor_col)["symbol"])
        if previous:
            rows[pd.Timestamp(trade_date)] = 1.0 - len(current & previous) / len(
                previous
            )
        previous = current
    result = pd.Series(rows, name="top_group_turnover", dtype=float)
    result.index.name = "trade_date"
    return result
