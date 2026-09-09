import tempfile
import unittest
from pathlib import Path

import pandas as pd

from quant_lab.data.daily_update import (
    consolidate_incremental_panel,
    incremental_zz500_update,
    latest_completed_session,
)


class DailySessionTest(unittest.TestCase):
    def test_waits_for_vendor_after_close(self) -> None:
        calendar = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(
                    ["2026-09-08", "2026-09-09", "2026-09-10"]
                ),
                "is_trading_day": [True, True, True],
            }
        )
        before_ready = latest_completed_session(
            calendar, pd.Timestamp("2026-09-10 09:00:00")
        )
        after_ready = latest_completed_session(
            calendar, pd.Timestamp("2026-09-10 19:00:00")
        )
        self.assertEqual(before_ready, pd.Timestamp("2026-09-09"))
        self.assertEqual(after_ready, pd.Timestamp("2026-09-10"))


class FakeDownloader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []

    def zz500_snapshot(self, trade_date: str | None = None) -> pd.DataFrame:
        effective = trade_date or "2025-01-20"
        return pd.DataFrame(
            {
                "trade_date": pd.to_datetime([effective, effective]),
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

    def test_existing_partitions_can_backfill_older_history(self) -> None:
        downloader = FakeDownloader()
        with tempfile.TemporaryDirectory() as temporary:
            incremental_zz500_update(
                downloader, temporary, end_date="2025-01-10", initial_calendar_days=10
            )
            incremental_zz500_update(
                downloader,
                temporary,
                end_date="2025-01-13",
                history_start_date="2024-12-01",
                membership_frequency="W-FRI",
            )
            panel = consolidate_incremental_panel(temporary)
            self.assertEqual(panel["trade_date"].min(), pd.Timestamp("2024-12-02"))
            self.assertEqual(panel["trade_date"].max(), pd.Timestamp("2025-01-13"))
            manifest = pd.read_json(Path(temporary) / "update_manifest.json", typ="series")
            self.assertEqual(manifest["history_start_date"], "2024-12-01")


if __name__ == "__main__":
    unittest.main()
