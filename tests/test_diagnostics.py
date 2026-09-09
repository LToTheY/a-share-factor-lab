import unittest

import pandas as pd

from quant_lab.evaluation.diagnostics import (
    add_forward_returns,
    information_coefficient,
)


class DiagnosticsTest(unittest.TestCase):
    def test_forward_return_never_crosses_symbol(self) -> None:
        dates = pd.bdate_range("2025-01-01", periods=3)
        frame = pd.DataFrame(
            {
                "trade_date": list(dates) * 2,
                "symbol": ["A"] * 3 + ["B"] * 3,
                "close": [10, 11, 12, 100, 90, 80],
            }
        )
        result = add_forward_returns(frame, periods=1)
        first = result.set_index(["symbol", "trade_date"])
        self.assertAlmostEqual(first.at[("A", dates[0]), "forward_return_1d"], 0.1)
        self.assertAlmostEqual(first.at[("B", dates[0]), "forward_return_1d"], -0.1)

    def test_rank_ic(self) -> None:
        frame = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(["2025-01-02"] * 10),
                "factor": range(10),
                "future": range(10),
            }
        )
        ic = information_coefficient(frame, "factor", "future")
        self.assertAlmostEqual(float(ic.iloc[0]), 1.0)


if __name__ == "__main__":
    unittest.main()
