import unittest

import numpy as np
import pandas as pd

from quant_lab.evaluation.preprocess import preprocess_factor


class PreprocessTest(unittest.TestCase):
    def test_processing_is_cross_sectional(self) -> None:
        frame = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(["2025-01-02"] * 6 + ["2025-01-03"] * 6),
                "symbol": list("ABCDEF") * 2,
                "factor": [1, 2, 3, 4, 5, 100, 10, 20, 30, 40, 50, 60],
                "market_cap": [1e9, 2e9, 3e9, 4e9, 5e9, 6e9] * 2,
                "industry": ["X", "X", "X", "Y", "Y", "Y"] * 2,
            }
        )
        result = preprocess_factor(
            frame, neutralize_size=False, neutralize_industry=False
        )
        means = result.groupby("trade_date")["factor_processed"].mean()
        self.assertTrue(np.allclose(means, 0.0, atol=1e-12))


if __name__ == "__main__":
    unittest.main()
