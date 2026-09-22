import unittest

import pandas as pd

from quant_lab.backtest.engine import BacktestConfig, run_backtest
from quant_lab.backtest.metrics import equal_weight_benchmark


class BacktestTest(unittest.TestCase):
    @staticmethod
    def _two_symbol_market() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "trade_date": pd.to_datetime(
                    ["2025-01-02", "2025-01-02", "2025-01-03", "2025-01-03"]
                ),
                "symbol": ["A", "B", "A", "B"],
                "open": [10.0, 10.0, 10.0, 10.0],
                "close": [10.0, 10.0, 10.0, 10.0],
            }
        )

    def test_signal_executes_next_session_not_same_day(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=3)
        market = pd.DataFrame(
            {
                "trade_date": dates,
                "symbol": ["A"] * 3,
                "open": [10.0, 11.0, 12.0],
                "close": [10.5, 11.5, 12.5],
                "is_suspended": False,
                "is_limit_up": False,
                "is_limit_down": False,
            }
        )
        weights = pd.DataFrame(
            {"trade_date": [dates[0]], "symbol": ["A"], "target_weight": [1.0]}
        )
        config = BacktestConfig(
            initial_cash=100_000,
            commission_rate=0,
            stamp_duty_rate=0,
            slippage_bps=0,
            minimum_commission=0,
            lot_size=100,
        )
        result = run_backtest(market, weights, config)
        self.assertEqual(result.trades.loc[0, "trade_date"], dates[1])
        self.assertEqual(result.trades.loc[0, "price"], 11.0)

    def test_limit_up_blocks_buy(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=2)
        market = pd.DataFrame(
            {
                "trade_date": dates,
                "symbol": ["A", "A"],
                "open": [10.0, 11.0],
                "close": [10.0, 11.0],
                "is_limit_up": [False, True],
            }
        )
        weights = pd.DataFrame(
            {"trade_date": [dates[0]], "symbol": ["A"], "target_weight": [1.0]}
        )
        result = run_backtest(market, weights)
        self.assertTrue(result.trades.empty)

    def test_stamp_duty_uses_historical_schedule(self) -> None:
        dates = pd.to_datetime(
            ["2023-08-24", "2023-08-25", "2023-08-28", "2023-08-29"]
        )
        market = pd.DataFrame(
            {
                "trade_date": dates,
                "symbol": ["A"] * 4,
                "open": [10.0] * 4,
                "close": [10.0] * 4,
            }
        )
        weights = pd.DataFrame(
            {
                "trade_date": [dates[0], dates[1], dates[2]],
                "symbol": ["A", "A", "A"],
                "target_weight": [1.0, 0.0, 1.0],
            }
        )
        config = BacktestConfig(
            commission_rate=0,
            transfer_fee_rate=0,
            minimum_commission=0,
            slippage_bps=0,
        )
        result = run_backtest(market, weights, config)
        sell = result.trades[result.trades["side"] == "SELL"].iloc[0]
        self.assertEqual(sell["trade_date"], pd.Timestamp("2023-08-28"))
        self.assertEqual(sell["tax_rate"], 0.0005)

    def test_stamp_duty_before_cut_uses_old_rate(self) -> None:
        dates = pd.to_datetime(["2023-08-23", "2023-08-24", "2023-08-25"])
        market = pd.DataFrame(
            {
                "trade_date": dates,
                "symbol": ["A"] * 3,
                "open": [10.0] * 3,
                "close": [10.0] * 3,
            }
        )
        weights = pd.DataFrame(
            {
                "trade_date": dates[:2],
                "symbol": ["A", "A"],
                "target_weight": [1.0, 0.0],
            }
        )
        config = BacktestConfig(
            commission_rate=0,
            transfer_fee_rate=0,
            minimum_commission=0,
            slippage_bps=0,
        )
        result = run_backtest(market, weights, config)
        sell = result.trades[result.trades["side"] == "SELL"].iloc[0]
        self.assertEqual(sell["trade_date"], pd.Timestamp("2023-08-25"))
        self.assertEqual(sell["tax_rate"], 0.001)

    def test_rejects_leveraged_or_negative_target_weights(self) -> None:
        market = self._two_symbol_market()
        leveraged = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(["2025-01-02", "2025-01-02"]),
                "symbol": ["A", "B"],
                "target_weight": [0.6, 0.5],
            }
        )
        with self.assertRaisesRegex(ValueError, "exceed 100%"):
            run_backtest(market, leveraged)
        negative = leveraged.iloc[[0]].copy()
        negative["target_weight"] = -0.1
        with self.assertRaisesRegex(ValueError, "cannot be negative"):
            run_backtest(market, negative)

    def test_rejects_duplicate_market_keys(self) -> None:
        market = pd.concat(
            [self._two_symbol_market(), self._two_symbol_market().iloc[[0]]],
            ignore_index=True,
        )
        weights = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(["2025-01-02"]),
                "symbol": ["A"],
                "target_weight": [1.0],
            }
        )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            run_backtest(market, weights)

    def test_rejects_invalid_backtest_costs(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be negative"):
            BacktestConfig(commission_rate=-0.001)

    def test_equal_weight_benchmark_does_not_cross_universe_gaps(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=3)
        market = pd.DataFrame(
            {
                "trade_date": list(dates) * 2,
                "symbol": ["A"] * 3 + ["B"] * 3,
                "adj_close": [10.0, 20.0, 20.0, 10.0, 10.0, 10.0],
                "in_universe": [True, False, True, True, True, True],
            }
        )
        benchmark = equal_weight_benchmark(market, 100.0)
        self.assertEqual(benchmark.loc[2, "return"], 0.0)
        self.assertEqual(benchmark.loc[2, "equity"], 100.0)

    def test_equal_weight_benchmark_rebases_at_requested_start(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=3)
        market = pd.DataFrame(
            {
                "trade_date": dates,
                "symbol": ["A"] * 3,
                "adj_close": [10.0, 11.0, 12.1],
                "in_universe": True,
            }
        )
        benchmark = equal_weight_benchmark(
            market,
            100.0,
            start_date=dates[1],
        )
        self.assertEqual(benchmark.iloc[0]["return"], 0.0)
        self.assertAlmostEqual(benchmark.iloc[0]["equity"], 100.0)
        self.assertAlmostEqual(benchmark.iloc[1]["equity"], 110.0)


if __name__ == "__main__":
    unittest.main()
