import unittest

import pandas as pd

from quant_lab.backtest.engine import BacktestConfig, run_backtest


class BacktestTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
