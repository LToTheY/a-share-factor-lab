import unittest

import numpy as np
import pandas as pd

from quant_lab.factors.library import compute_factor, momentum


class FactorTest(unittest.TestCase):
    def test_momentum_is_grouped_by_symbol(self) -> None:
        dates = pd.bdate_range("2024-01-01", periods=70)
        frame = pd.concat(
            [
                pd.DataFrame(
                    {
                        "trade_date": dates,
                        "symbol": symbol,
                        "close": base * np.arange(1, 71),
                        "adj_factor": 1.0,
                    }
                )
                for symbol, base in [("A", 1.0), ("B", 1000.0)]
            ],
            ignore_index=True,
        )
        result = compute_factor(frame, "momentum_60_5")
        latest = result[result["trade_date"] == dates[-1]].set_index("symbol")
        self.assertAlmostEqual(latest.at["A", "factor"], latest.at["B", "factor"])
        self.assertAlmostEqual(latest.at["A", "factor"], 65 / 10 - 1)

    def test_unknown_factor_fails(self) -> None:
        with self.assertRaises(KeyError):
            compute_factor(pd.DataFrame(), "magic_alpha")

    def test_momentum_window_validation(self) -> None:
        with self.assertRaises(ValueError):
            momentum(pd.DataFrame(), lookback=5, skip=5)


if __name__ == "__main__":
    unittest.main()
