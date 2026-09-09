"""Leakage-aware rolling factor selection and out-of-sample evaluation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from quant_lab.backtest.engine import BacktestConfig, run_backtest
from quant_lab.backtest.metrics import performance_metrics, turnover_from_trades
from quant_lab.evaluation.diagnostics import information_coefficient, summarize_ic
from quant_lab.evaluation.preprocess import zscore
from quant_lab.portfolio.weights import buffered_top_n_weights
from quant_lab.research.settings import ResearchSettings


def _drop_last_dates(frame: pd.DataFrame, count: int) -> pd.DataFrame:
    if count <= 0 or frame.empty:
        return frame
    dates = pd.DatetimeIndex(sorted(frame["trade_date"].unique()))
    if len(dates) <= count:
        return frame.iloc[0:0]
    return frame[frame["trade_date"].isin(dates[:-count])]


def _mean_ic(frame: pd.DataFrame, factor: str, label_col: str) -> float:
    if frame.empty:
        return float("nan")
    return float(information_coefficient(frame, factor, label_col).mean())


def run_walk_forward(
    scores: pd.DataFrame,
    market: pd.DataFrame,
    factor_names: list[str],
    label_col: str,
    settings: ResearchSettings,
    output_dir: str | Path,
    next_trading_date: str | pd.Timestamp | None = None,
) -> dict[str, Any]:
    """Select factors on past windows and score only the following test year."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    config = settings.validation
    train_years = int(config.get("train_years", 5))
    validation_years = int(config.get("validation_years", 1))
    test_years = int(config.get("test_years", 1))
    step_years = int(config.get("step_years", test_years))
    if min(train_years, validation_years, test_years, step_years) < 1:
        raise ValueError("All walk-forward window lengths must be positive")
    embargo = int(config.get("embargo_trading_days", settings.forward_periods))
    work = scores.copy()
    work["trade_date"] = pd.to_datetime(work["trade_date"])
    first_year = pd.Timestamp(
        settings.research_start_date or work["trade_date"].min()
    ).year
    last_year = int(work["trade_date"].dt.year.max())
    first_test_year = first_year + train_years + validation_years
    predictions = []
    fold_rows = []

    for test_year in range(first_test_year, last_year + 1, step_years):
        test_end_year = min(test_year + test_years, last_year + 1)
        validation_start = test_year - validation_years
        train_start = validation_start - train_years
        train = work[
            (work["trade_date"].dt.year >= train_start)
            & (work["trade_date"].dt.year < validation_start)
        ]
        validation = work[
            (work["trade_date"].dt.year >= validation_start)
            & (work["trade_date"].dt.year < test_year)
        ]
        test = work[
            (work["trade_date"].dt.year >= test_year)
            & (work["trade_date"].dt.year < test_end_year)
        ].copy()
        train = _drop_last_dates(train, embargo)
        validation = _drop_last_dates(validation, embargo)
        if train.empty or validation.empty or test.empty:
            continue

        diagnostics = []
        for factor in factor_names:
            train_ic = _mean_ic(train, factor, label_col)
            validation_ic = _mean_ic(validation, factor, label_col)
            diagnostics.append((factor, train_ic, validation_ic))
        selected = [
            (factor, validation_ic)
            for factor, train_ic, validation_ic in diagnostics
            if pd.notna(train_ic)
            and pd.notna(validation_ic)
            and train_ic > 0
            and validation_ic > 0
        ]
        if not selected:
            fold_rows.append(
                {
                    "test_year": test_year,
                    "test_end_year": test_end_year - 1,
                    "train_start_year": train_start,
                    "validation_start_year": validation_start,
                    "selected_factors": "",
                    "selected_count": 0,
                    "test_mean_ic": float("nan"),
                }
            )
            continue
        total = sum(value for _, value in selected)
        weights = {factor: value / total for factor, value in selected}
        numerator = sum(test[factor].fillna(0.0) * weight for factor, weight in weights.items())
        available_weight = sum(
            test[factor].notna().astype(float) * weight
            for factor, weight in weights.items()
        )
        test["factor_processed"] = numerator.div(available_weight.replace(0, pd.NA))
        test.loc[
            test[list(weights)].notna().sum(axis=1)
            < min(settings.minimum_valid_factors, len(weights)),
            "factor_processed",
        ] = pd.NA
        test["factor_processed"] = test.groupby("trade_date")[
            "factor_processed"
        ].transform(zscore)
        test["walk_forward_fold"] = test_year
        predictions.append(test)
        test_ic = information_coefficient(test, "factor_processed", label_col)
        fold_rows.append(
            {
                "test_year": test_year,
                "test_end_year": test_end_year - 1,
                "train_start_year": train_start,
                "validation_start_year": validation_start,
                "selected_factors": ",".join(weights),
                "selected_count": len(weights),
                "test_mean_ic": float(test_ic.mean()),
            }
        )

    folds = pd.DataFrame(fold_rows)
    folds.to_csv(output / "fold_summary.csv", index=False, encoding="utf-8-sig")
    if not predictions:
        summary = {"status": "INSUFFICIENT_HISTORY", "folds": len(folds)}
        (output / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return summary

    oos = pd.concat(predictions, ignore_index=True).sort_values(
        ["trade_date", "symbol"]
    )
    oos_ic = information_coefficient(oos, "factor_processed", label_col)
    targets = buffered_top_n_weights(
        oos,
        top_n=settings.top_n,
        exit_rank=settings.exit_rank,
        frequency=settings.rebalance_frequency,
        max_weight=settings.max_weight,
        next_trading_date=next_trading_date,
    )
    start = oos["trade_date"].min()
    oos_market = market[pd.to_datetime(market["trade_date"]) >= start]
    result = run_backtest(oos_market, targets, BacktestConfig(**settings.backtest))
    portfolio = performance_metrics(result.equity)
    portfolio["turnover"] = turnover_from_trades(result.trades, result.equity)
    summary = {
        "status": "OK",
        "folds": len(folds),
        "start_date": str(pd.Timestamp(oos["trade_date"].min()).date()),
        "end_date": str(pd.Timestamp(oos["trade_date"].max()).date()),
        "ic": summarize_ic(oos_ic, periods_per_year=252 / settings.forward_periods),
        "portfolio": portfolio,
        "embargo_trading_days": embargo,
    }
    oos.to_parquet(output / "oos_scores.parquet", index=False)
    oos_ic.rename_axis("trade_date").reset_index().to_csv(
        output / "oos_ic.csv", index=False
    )
    targets.to_csv(output / "target_weights.csv", index=False)
    result.equity.to_csv(output / "equity.csv", index=False)
    result.trades.to_csv(output / "trades.csv", index=False)
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=True),
        encoding="utf-8",
    )
    return summary
