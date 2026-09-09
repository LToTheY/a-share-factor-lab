import tempfile
import unittest
from pathlib import Path

import pandas as pd

from quant_lab.data.daily_update import (
    consolidate_incremental_panel,
    incremental_zz500_update,
)


class FakeDownloader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []

    def zz500_snapshot(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "trade_date": pd.to_datetime(["2025-01-20", "2025-01-20"]),
                "symbol": ["600000.SH", "000001.SZ"],
                "weight": [None, None],
            }
        )

    def daily_history(
        self,
        code: str,
        start_date: str,
        end_date: str,
        adjustflag: str,
        include_adjustment_factors: bool = False,
    ) -> pd.DataFrame:
        self.calls.append((code, start_date, end_date, adjustflag))
        dates = pd.bdate_range(start_date, end_date)
        symbol = code.split(".")[1] + "." + code.split(".")[0].upper()
        price = 20.0 if adjustflag == "1" else 10.0
        return pd.DataFrame(
            {
                "trade_date": dates,
                "symbol": symbol,
                "open": price,
                "high": price + 0.5,
                "low": price - 0.5,
                "close": price,
                "preclose": price,
                "volume": 1000.0,
                "amount": 10000.0,
                "adj_factor": 1.0,
                "is_st": False,
                "is_suspended": False,
                "is_limit_up": False,
                "is_limit_down": False,
                "is_st_known": True,
                "is_suspended_known": True,
                "limit_status_known": True,
            }
        )

    def trade_calendar(self, start_date: str, end_date: str) -> pd.DataFrame:
        dates = pd.date_range(start_date, end_date)
        return pd.DataFrame(
            {"trade_date": dates, "is_trading_day": dates.dayofweek < 5}
        )


class DailyUpdateTest(unittest.TestCase):
    def test_second_run_requests_only_missing_dates(self) -> None:
        downloader = FakeDownloader()
        with tempfile.TemporaryDirectory() as temporary:
            incremental_zz500_update(
                downloader, temporary, end_date="2025-01-10", initial_calendar_days=10
            )
            first_call_count = len(downloader.calls)
            incremental_zz500_update(
                downloader, temporary, end_date="2025-01-13", initial_calendar_days=10
            )
            second_calls = downloader.calls[first_call_count:]
            self.assertEqual(len(second_calls), 4)
            self.assertTrue(
                all(start == "2025-01-11" for _, start, _, _ in second_calls)
            )
            panel = consolidate_incremental_panel(Path(temporary))
            self.assertEqual(panel["trade_date"].max(), pd.Timestamp("2025-01-13"))
            self.assertEqual(panel.duplicated(["trade_date", "symbol"]).sum(), 0)
            self.assertTrue((panel["close"] == 10.0).all())
            self.assertTrue((panel["adj_close"] == 20.0).all())
            self.assertTrue(panel["in_index"].all())


if __name__ == "__main__":
    unittest.main()
