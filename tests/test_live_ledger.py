import tempfile
import unittest
from pathlib import Path

import pandas as pd

from quant_lab.research.live_ledger import record_live_snapshot


class LiveLedgerTest(unittest.TestCase):
    def test_snapshot_is_write_once_and_verifiable(self) -> None:
        latest = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(["2026-09-10"]),
                "symbol": ["600000.SH"],
                "factor_rank": [1.0],
                "factor_processed": [2.0],
                "close": [10.0],
            }
        )
        orders = pd.DataFrame({"status": ["NO_TRADE"], "symbol": [None]})
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.yaml"
            config.write_text("version: 1\n", encoding="utf-8")
            first = record_live_snapshot(
                latest, orders, config, temporary, "2026-09-10"
            )
            second = record_live_snapshot(
                latest, orders, config, temporary, "2026-09-10"
            )
            self.assertEqual(first["status"], "RECORDED")
            self.assertEqual(second["status"], "VERIFIED_EXISTING")

            changed = latest.copy()
            changed["factor_processed"] = 3.0
            with self.assertRaises(RuntimeError):
                record_live_snapshot(
                    changed, orders, config, temporary, "2026-09-10"
                )


if __name__ == "__main__":
    unittest.main()
