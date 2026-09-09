"""End-to-end zero-token demo and reusable research orchestration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from quant_lab.backtest.engine import BacktestConfig, run_backtest
from quant_lab.backtest.metrics import performance_metrics, turnover_from_trades
from quant_lab.data.synthetic import make_synthetic_daily_data
from quant_lab.evaluation.diagnostics import (
    add_forward_returns,
    information_coefficient,
    quantile_returns,
    summarize_ic,
)
from quant_lab.evaluation.preprocess import preprocess_factor
from quant_lab.factors.library import compute_factor
from quant_lab.models.dataset import (
    chronological_split,
    combine_factor_tables,
    cross_sectional_rank_label,
)
from quant_lab.models.ridge import RidgeRegressor, prediction_rank_ic
from quant_lab.portfolio.weights import top_n_weights
from quant_lab.universe.filters import UniverseConfig, apply_universe

DEMO_FACTORS = ["momentum_60_5", "reversal_5", "amihud_20", "volatility_20"]


def prepare_factor(
    market: pd.DataFrame,
    name: str,
    forward_periods: int = 5,
) -> pd.DataFrame:
    """Compute, merge exposures, preprocess, label and return one research table."""
    signal = compute_factor(market, name)
    market = market.copy()
    market["adj_close"] = market["close"] * market.get("adj_factor", 1.0)
    exposures = market[["trade_date", "symbol", "market_cap", "industry", "adj_close"]]
    signal = signal.merge(
        exposures, on=["trade_date", "symbol"], how="left", validate="one_to_one"
    )
    signal = preprocess_factor(signal)
    labeled_market = add_forward_returns(market, forward_periods, price_col="adj_close")
    return signal.merge(
        labeled_market[["trade_date", "symbol", f"forward_return_{forward_periods}d"]],
        on=["trade_date", "symbol"],
        how="left",
        validate="one_to_one",
    )


def run_demo(
    output_dir: str | Path,
    seed: int = 42,
    n_symbols: int = 40,
    n_days: int = 700,
) -> dict[str, Any]:
    """Run data→factor→IC→backtest→Ridge and persist inspectable artifacts."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    market = make_synthetic_daily_data(n_symbols, n_days, seed=seed)
    market = apply_universe(
        market,
        UniverseConfig(min_listed_days=120, min_amount=1_000_000),
    )

    research = prepare_factor(market, "momentum_60_5", forward_periods=5)
    ic = information_coefficient(
        research, "factor_processed", "forward_return_5d", min_observations=10
    )
    ic_summary = summarize_ic(ic)
    groups = quantile_returns(
        research, "factor_processed", "forward_return_5d", groups=5
    )

    weights = top_n_weights(
        research, signal_col="factor_processed", top_n=10, max_weight=0.10
    )
    backtest = run_backtest(
        market,
        weights,
        BacktestConfig(initial_cash=1_000_000, slippage_bps=5.0),
    )
    portfolio_metrics = performance_metrics(backtest.equity)
    portfolio_metrics["turnover"] = turnover_from_trades(
        backtest.trades, backtest.equity
    )

    factor_tables = [compute_factor(market, name) for name in DEMO_FACTORS]
    model_frame = combine_factor_tables(factor_tables, DEMO_FACTORS)
    label_market = market.copy()
    label_market["adj_close"] = label_market["close"] * label_market.get(
        "adj_factor", 1.0
    )
    labels = add_forward_returns(label_market, 5, price_col="adj_close")[
        ["trade_date", "symbol", "forward_return_5d"]
    ]
    model_frame = model_frame.merge(
        labels, on=["trade_date", "symbol"], how="left", validate="one_to_one"
    )
    model_frame = cross_sectional_rank_label(model_frame, "forward_return_5d")

    unique_dates = sorted(model_frame["trade_date"].dropna().unique())
    train_end = pd.Timestamp(unique_dates[int(len(unique_dates) * 0.60)])
    validation_end = pd.Timestamp(unique_dates[int(len(unique_dates) * 0.80)])
    split = chronological_split(
        model_frame,
        str(train_end.date()),
        str(validation_end.date()),
        embargo_days=7,
    )
    ridge = RidgeRegressor(alpha=10.0).fit(
        split.train[DEMO_FACTORS], split.train["label"]
    )
    test = split.test.copy()
    test["prediction"] = ridge.predict(test[DEMO_FACTORS])
    model_ic = prediction_rank_ic(test).dropna()
    model_summary = {
        "test_mean_rank_ic": float(model_ic.mean()),
        "test_rank_ic_std": float(model_ic.std(ddof=1)),
        "test_observation_dates": int(model_ic.count()),
    }

    research.to_csv(output / "factor_research.csv", index=False, encoding="utf-8-sig")
    ic.rename_axis("trade_date").reset_index().to_csv(
        output / "rank_ic.csv", index=False
    )
    groups.to_csv(output / "quantile_returns.csv", index=False)
    weights.to_csv(output / "target_weights.csv", index=False)
    backtest.equity.to_csv(output / "equity.csv", index=False)
    backtest.trades.to_csv(output / "trades.csv", index=False)
    test[["trade_date", "symbol", "label", "prediction"]].to_csv(
        output / "model_predictions.csv", index=False
    )
    summary = {
        "warning": "Synthetic data validates code only; it is not investment evidence.",
        "factor": ic_summary,
        "portfolio": portfolio_metrics,
        "model": model_summary,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=True),
        encoding="utf-8",
    )
    return summary
