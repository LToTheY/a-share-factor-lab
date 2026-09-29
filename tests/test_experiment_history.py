from dataclasses import asdict

import pandas as pd
import pytest

from quant_lab.backtest.engine import BacktestConfig, run_backtest
from quant_lab.dashboard import ArtifactError
from quant_lab.dashboard.experiments import (
    ExperimentStore,
    frame_fingerprint,
    replay_day,
)
from quant_lab.strategy.sandbox import StrategySpec, run_strategy


def sample_experiment():
    dates = pd.bdate_range("2025-01-02", periods=4)
    market = pd.DataFrame(
        [
            {
                "trade_date": date,
                "symbol": symbol,
                "open": price,
                "close": price,
                "adj_close": price,
                "in_index": True,
            }
            for date in dates
            for symbol, price in (("A", 10.0), ("B", 20.0))
        ]
    )
    scores = market[["trade_date", "symbol"]].assign(
        in_universe=True, alpha=[1.0, -1.0] * 4
    )
    spec = StrategySpec(
        {"alpha": 1.0}, top_n=1, exit_rank=1, max_weight=1.0, rebalance_frequency="D"
    )
    config = BacktestConfig(initial_cash=10000)
    result = run_strategy(scores, market, spec, config)
    return (
        result,
        spec,
        config,
        {
            "market_fingerprint": frame_fingerprint(market),
            "score_fingerprint": frame_fingerprint(scores),
        },
    )


def test_saved_experiment_survives_reload_and_preserves_ledgers(tmp_path):
    result, spec, config, source = sample_experiment()
    identifier = ExperimentStore(tmp_path).save(
        result, spec, config, source, "动量测试"
    )
    storage = ExperimentStore(tmp_path)
    records, errors = storage.list()
    assert not errors
    assert records[0]["strategy"] == asdict(spec)
    assert records[0]["backtest"] == asdict(config)
    pd.testing.assert_frame_equal(
        storage.table(identifier, "trades"), result.backtest.trades
    )
    pd.testing.assert_frame_equal(
        storage.table(identifier, "execution_issues"), result.backtest.execution_issues
    )
    assert storage.save(result, spec, config, source, "动量测试") != identifier
    assert len(storage.list()[0]) == 2
    with pytest.raises(ArtifactError):
        storage.table("../secret", "trades")
    with pytest.raises(ArtifactError):
        storage.table(identifier, "../secret")


def test_incomplete_and_corrupt_snapshots_do_not_hide_valid_experiment(tmp_path):
    result, spec, config, source = sample_experiment()
    storage = ExperimentStore(tmp_path)
    storage.save(result, spec, config, source, "valid")
    (tmp_path / ".pending-test").mkdir()
    broken = tmp_path / ("a" * 32)
    broken.mkdir()
    (broken / "metadata.json").write_text("invalid", encoding="utf-8")
    records, errors = storage.list()
    assert len(records) == len(errors) == 1


def test_replay_uses_previous_signal_not_same_day_target():
    result, _, _, _ = sample_experiment()
    dates = result.backtest.equity.trade_date.tolist()
    day = replay_day(
        result.backtest.equity,
        result.targets,
        result.backtest.trades,
        result.backtest.positions,
        result.backtest.execution_issues,
        dates[1],
    )
    assert day["signal_date"] == dates[0]
    assert set(day["targets"].trade_date) == {dates[0]}
    assert set(day["trades"].trade_date) == {dates[1]}
    first = replay_day(
        result.backtest.equity,
        result.targets,
        result.backtest.trades,
        result.backtest.positions,
        result.backtest.execution_issues,
        dates[0],
    )
    assert first["targets"].empty and first["signal_date"] is None


@pytest.mark.parametrize(
    "flag,reason", [("is_limit_up", "涨停禁止买入"), ("is_suspended", "停牌")]
)
def test_blocked_buys_record_reason_and_preserve_cash(flag, reason):
    dates = pd.bdate_range("2025-01-02", periods=2)
    market = pd.DataFrame(
        {
            "trade_date": dates,
            "symbol": "A",
            "open": 10.0,
            "close": 10.0,
            flag: [False, True],
        }
    )
    targets = pd.DataFrame(
        {"trade_date": [dates[0]], "symbol": "A", "target_weight": 1.0}
    )
    result = run_backtest(market, targets)
    assert result.trades.empty
    assert result.equity.cash.iloc[-1] == 1000000
    assert result.execution_issues.reason.tolist() == [reason]
    assert result.execution_issues.signal_date.iloc[0] == dates[0]


def test_partial_fill_records_unfilled_lots():
    dates = pd.bdate_range("2025-01-02", periods=2)
    market = pd.DataFrame(
        {"trade_date": dates, "symbol": "A", "open": 10.0, "close": 10.0}
    )
    targets = pd.DataFrame(
        {"trade_date": [dates[0]], "symbol": "A", "target_weight": 1.0}
    )
    result = run_backtest(market, targets, BacktestConfig(initial_cash=10000))
    assert result.trades.shares.iloc[0] == 900
    assert result.execution_issues.unfilled_shares.iloc[0] == 100
    assert result.execution_issues.reason.iloc[0] == "现金不足（含费用）"


def test_limit_down_records_failed_exit():
    dates = pd.bdate_range("2025-01-02", periods=3)
    market = pd.DataFrame(
        {
            "trade_date": dates,
            "symbol": "A",
            "open": 10.0,
            "close": 10.0,
            "is_limit_down": [False, False, True],
        }
    )
    targets = pd.DataFrame(
        {"trade_date": dates[:2], "symbol": "A", "target_weight": [1.0, 0.0]}
    )
    result = run_backtest(market, targets)
    assert "跌停禁止卖出" in result.execution_issues.reason.tolist()
    assert result.trades.side.tolist() == ["BUY"]
    assert result.positions.shares.iloc[-1] > 0
