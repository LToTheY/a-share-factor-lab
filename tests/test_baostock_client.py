import unittest

import pandas as pd

from quant_lab.data.baostock_client import (
    baostock_to_symbol,
    derive_open_limit_flags,
    price_limit_ratio,
    symbol_to_baostock,
)


class BaoStockAdapterTest(unittest.TestCase):
    def test_symbol_conversion_round_trip(self) -> None:
        self.assertEqual(baostock_to_symbol("sh.600000"), "600000.SH")
        self.assertEqual(symbol_to_baostock("000001.SZ"), "sz.000001")

    def test_historical_board_limit_rules(self) -> None:
        self.assertEqual(
            price_limit_ratio("300001.SZ", pd.Timestamp("2020-08-21"), False), 0.10
        )
        self.assertEqual(
            price_limit_ratio("300001.SZ", pd.Timestamp("2020-08-24"), False), 0.20
        )
        self.assertEqual(
            price_limit_ratio("688001.SH", pd.Timestamp("2020-01-01"), False), 0.20
        )
        self.assertEqual(
            price_limit_ratio("600000.SH", pd.Timestamp("2025-01-01"), True), 0.05
        )

    def test_open_at_limit_is_blocked(self) -> None:
        frame = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(["2025-01-02", "2025-01-02"]),
                "symbol": ["600000.SH", "600001.SH"],
                "preclose": [10.0, 10.0],
                "open": [11.0, 10.5],
                "is_st": [False, True],
            }
        )
        result = derive_open_limit_flags(frame)
        self.assertTrue(bool(result.loc[0, "is_limit_up"]))
        self.assertTrue(bool(result.loc[1, "is_limit_up"]))
        self.assertTrue(bool(result["limit_status_known"].all()))

    def test_first_five_sessions_can_be_unlimited_under_new_rules(self) -> None:
        self.assertIsNone(
            price_limit_ratio(
                "688001.SH", pd.Timestamp("2019-07-22"), False, listing_age_sessions=0
            )
        )
        self.assertIsNone(
            price_limit_ratio(
                "300001.SZ", pd.Timestamp("2020-08-24"), False, listing_age_sessions=4
            )
        )
        self.assertEqual(
            price_limit_ratio(
                "300001.SZ", pd.Timestamp("2020-08-24"), False, listing_age_sessions=5
            ),
            0.20,
        )

    def test_adjusted_price_limit_uses_return_ratio(self) -> None:
        frame = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(["2025-01-02"]),
                "symbol": ["600000.SH"],
                "preclose": [123.456],
                "open": [135.8016],
                "is_st": [False],
            }
        )
        result = derive_open_limit_flags(frame, adjusted_prices=True)
        self.assertTrue(bool(result.loc[0, "is_limit_up"]))


if __name__ == "__main__":
    unittest.main()
