import unittest

import pandas as pd

from quant_lab.data.missing import classify_market_rows


class MissingClassificationTest(unittest.TestCase):
    def test_suspension_and_provider_gap_are_distinct(self) -> None:
        frame = pd.DataFrame(
            {
                "open": [10.0, None],
                "high": [10.0, None],
                "low": [10.0, None],
                "close": [10.0, None],
                "volume": [0.0, None],
                "amount": [0.0, None],
                "adj_open": [10.0, None],
                "adj_high": [10.0, None],
                "adj_low": [10.0, None],
                "adj_close": [10.0, None],
                "is_suspended": [True, False],
            }
        )
        result = classify_market_rows(frame)
        self.assertEqual(result.loc[0, "data_quality_reason"], "SUSPENDED")
        self.assertEqual(result.loc[1, "data_quality_reason"], "PROVIDER_GAP")
        self.assertFalse(bool(result.loc[1, "is_usable_market_data"]))


if __name__ == "__main__":
    unittest.main()
