import unittest

import pandas as pd

from quant_lab.portfolio.paper import build_next_day_orders
from quant_lab.portfolio.weights import buffered_top_n_weights, rebalance_dates


class PortfolioDailyTest(unittest.TestCase):
    def test_incomplete_week_is_not_rebalance_date(self) -> None:
        dates = pd.Series(pd.to_datetime(["2025-01-06", "2025-01-07"]))
        selected = rebalance_dates(
            dates, "W-FRI", next_trading_date=pd.Timestamp("2025-01-08")
        )
        self.assertTrue(selected.empty)

    def test_daily_buffer_avoids_unnecessary_turnover(self) -> None:
        signals = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(["2025-01-02"] * 4 + ["2025-01-03"] * 4),
                "symbol": ["A", "B", "C", "D"] * 2,
                "factor_processed": [4, 3, 2, 1, 3, 4, 2, 1],
            }
        )
        weights = buffered_top_n_weights(
            signals, top_n=2, exit_rank=3, frequency="D", max_weight=0.5
        )
        holdings = weights.groupby("trade_date")["symbol"].apply(set)
        self.assertEqual(holdings.iloc[0], {"A", "B"})
        self.assertEqual(holdings.iloc[1], {"A", "B"})

    def test_weekday_plan_can_explicitly_say_no_trade(self) -> None:
        latest = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(["2025-01-07"]),
                "symbol": ["A"],
                "close": [10.0],
                "factor_rank": [1.0],
            }
        )
        orders = build_next_day_orders(
            latest,
            pd.DataFrame(),
            {"cash": 1_000_000.0, "positions": {}},
            "2025-01-08",
            "W-FRI",
        )
        self.assertEqual(orders.iloc[0]["status"], "NO_TRADE")

    def test_rebalance_plan_sizes_with_raw_reference_close(self) -> None:
        latest = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(["2025-01-10"]),
                "symbol": ["A"],
                "close": [10.0],
                "factor_rank": [1.0],
            }
        )
        targets = pd.DataFrame(
            {"symbol": ["A"], "target_weight": [0.05], "factor_rank": [1.0]}
        )
        orders = build_next_day_orders(
            latest,
            targets,
            {"cash": 1_000_000.0, "positions": {}},
            "2025-01-13",
            "W-FRI",
        )
        self.assertEqual(orders.iloc[0]["status"], "REVIEW_REQUIRED")
        self.assertEqual(orders.iloc[0]["target_shares"], 5000)

    def test_positions_outside_signal_universe_are_included_in_account_value(self) -> None:
        latest = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(["2025-01-10"]),
                "symbol": ["A"],
                "close": [10.0],
                "factor_rank": [1.0],
            }
        )
        targets = pd.DataFrame(
            {"symbol": ["A"], "target_weight": [0.5], "factor_rank": [1.0]}
        )
        prices = pd.DataFrame({"symbol": ["A", "B"], "close": [10.0, 20.0]})
        orders = build_next_day_orders(
            latest,
            targets,
            {"cash": 0.0, "positions": {"B": 100}},
            "2025-01-13",
            "W-FRI",
            reference_prices=prices,
        )
        buy = orders[(orders["symbol"] == "A") & (orders["side"] == "BUY")].iloc[0]
        self.assertEqual(buy["target_shares"], 100)

    def test_missing_held_position_price_blocks_order_plan(self) -> None:
        latest = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(["2025-01-10"]),
                "symbol": ["A"],
                "close": [10.0],
                "factor_rank": [1.0],
            }
        )
        orders = build_next_day_orders(
            latest,
            pd.DataFrame(),
            {"cash": 0.0, "positions": {"B": 100}},
            "2025-01-13",
            "W-FRI",
        )
        self.assertEqual(orders.iloc[0]["status"], "PRICE_MISSING")


if __name__ == "__main__":
    unittest.main()
