import unittest

import pandas as pd

from quant_lab.universe.filters import attach_index_membership


class UniverseTest(unittest.TestCase):
    def test_index_snapshot_never_looks_forward(self) -> None:
        dates = pd.to_datetime(["2025-01-15", "2025-02-15"])
        market = pd.DataFrame(
            {
                "trade_date": [dates[0], dates[0], dates[1], dates[1]],
                "symbol": ["A", "B", "A", "B"],
            }
        )
        weights = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(["2025-01-01", "2025-02-01"]),
                "symbol": ["A", "B"],
                "weight": [1.0, 1.0],
            }
        )
        result = attach_index_membership(market, weights)
        first = result[result["trade_date"] == dates[0]].set_index("symbol")
        second = result[result["trade_date"] == dates[1]].set_index("symbol")
        self.assertTrue(bool(first.at["A", "in_index"]))
        self.assertFalse(bool(first.at["B", "in_index"]))
        self.assertFalse(bool(second.at["A", "in_index"]))
        self.assertTrue(bool(second.at["B", "in_index"]))


if __name__ == "__main__":
    unittest.main()
