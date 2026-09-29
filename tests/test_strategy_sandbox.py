import pandas as pd
import pytest

from quant_lab.backtest.engine import BacktestConfig
from quant_lab.dashboard import ArtifactError
from quant_lab.dashboard.strategy_data import StrategyDataStore
from quant_lab.strategy.sandbox import (
    StrategySpec,
    annual_returns,
    build_composite_signal,
    run_strategy,
)


def _scores() -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=5)
    rows = []
    for day, date in enumerate(dates):
        for symbol, factor_a, factor_b in (
            ("A", 2.0, 1.0),
            ("B", 1.0, 2.0),
            ("C", 0.0, 0.0),
        ):
            rows.append(
                {
                    "trade_date": date,
                    "symbol": symbol,
                    "factor_a": factor_a + day * 0.01,
                    "factor_b": factor_b,
                    "in_universe": True,
                }
            )
    return pd.DataFrame(rows)


def _market() -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=5)
    rows = []
    for day, date in enumerate(dates):
        for symbol, base in (("A", 10.0), ("B", 20.0), ("C", 30.0)):
            rows.append(
                {
                    "trade_date": date,
                    "symbol": symbol,
                    "open": base + day,
                    "close": base + day + 0.5,
                    "adj_close": base + day + 0.5,
                    "in_index": True,
                    "is_suspended": False,
                    "is_limit_up": False,
                    "is_limit_down": False,
                }
            )
    return pd.DataFrame(rows)


def test_composite_uses_signed_weights_and_universe_filter() -> None:
    scores = _scores()
    scores.loc[(scores["symbol"] == "C"), "in_universe"] = False
    spec = StrategySpec(
        factor_weights={"factor_a": 1.0, "factor_b": -1.0},
        top_n=1,
        exit_rank=2,
        max_weight=1.0,
    )

    result = build_composite_signal(scores, spec)

    first = result[result["trade_date"] == result["trade_date"].min()].set_index(
        "symbol"
    )
    assert first.loc["A", "factor_processed"] > first.loc["B", "factor_processed"]
    assert pd.isna(first.loc["C", "factor_processed"])


def test_strategy_runs_existing_next_open_backtest() -> None:
    spec = StrategySpec(
        factor_weights={"factor_a": 1.0},
        top_n=1,
        exit_rank=2,
        rebalance_frequency="D",
        max_weight=1.0,
    )
    config = BacktestConfig(
        initial_cash=100_000,
        commission_rate=0,
        stamp_duty_rate=0,
        historical_stamp_duty_rate=0,
        transfer_fee_rate=0,
        slippage_bps=0,
        minimum_commission=0,
    )

    result = run_strategy(_scores(), _market(), spec, config)

    assert not result.backtest.trades.empty
    assert result.backtest.trades.iloc[0]["trade_date"] > result.targets.iloc[0][
        "trade_date"
    ]
    assert result.diagnostics["trade_count"] == len(result.backtest.trades)
    assert "annual_return" in result.metrics


def test_strategy_spec_rejects_unsafe_or_empty_rules() -> None:
    with pytest.raises(ValueError, match="至少需要"):
        StrategySpec(factor_weights={"x": 0.0})
    with pytest.raises(ValueError, match="exit_rank"):
        StrategySpec(factor_weights={"x": 1.0}, top_n=20, exit_rank=10)


def test_annual_returns_compounds_daily_changes() -> None:
    equity = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(
                ["2024-12-31", "2025-01-02", "2025-01-03"]
            ),
            "equity": [100.0, 110.0, 121.0],
        }
    )
    result = annual_returns(equity).set_index("year")
    assert result.loc[2025, "return"] == pytest.approx(0.21)


def test_strategy_data_store_selects_dates_and_rejects_unknown_factor(tmp_path) -> None:
    scores = _scores()
    market = _market()
    score_path = tmp_path / "scores.parquet"
    market_path = tmp_path / "market.parquet"
    scores.to_parquet(score_path, index=False)
    market.to_parquet(market_path, index=False)
    data = StrategyDataStore(score_path, market_path)

    start, end = data.date_bounds()
    selected_scores, selected_market = data.load(
        ["factor_a"], start, end, allowed_factors=["factor_a", "factor_b"]
    )

    assert "factor_b" not in selected_scores
    assert set(selected_market.columns) == {
        "trade_date",
        "symbol",
        "open",
        "close",
        "adj_close",
        "in_index",
        "is_suspended",
        "is_limit_up",
        "is_limit_down",
    }
    with pytest.raises(ArtifactError, match="未知因子"):
        data.load(["bad_factor"], start, end, allowed_factors=["factor_a"])
