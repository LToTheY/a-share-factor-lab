import unittest

import numpy as np
import pandas as pd

from quant_lab.data.panel import fill_explicit_suspensions


class PanelTest(unittest.TestCase):
    def test_only_explicit_suspension_is_filled(self) -> None:
        frame = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(
                    ["2025-01-02", "2025-01-03", "2025-01-06"]
                ),
                "symbol": ["A", "A", "A"],
                "open": [10.0, np.nan, np.nan],
                "high": [10.0, np.nan, np.nan],
                "low": [10.0, np.nan, np.nan],
                "close": [10.0, np.nan, np.nan],
                "volume": [100.0, np.nan, np.nan],
                "amount": [1000.0, np.nan, np.nan],
                "is_suspended": [False, True, False],
            }
        )
        result = fill_explicit_suspensions(frame)
        self.assertEqual(result.loc[1, "close"], 10.0)
        self.assertEqual(result.loc[1, "amount"], 0.0)
        self.assertTrue(np.isnan(result.loc[2, "close"]))


if __name__ == "__main__":
    unittest.main()
