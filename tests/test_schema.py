import unittest

import pandas as pd

from quant_lab.data.schema import normalize_daily_frame, validate_daily_frame


class SchemaTest(unittest.TestCase):
    def setUp(self) -> None:
        self.frame = pd.DataFrame(
            {
                "trade_date": ["2025-01-02"],
                "symbol": ["000001.SZ"],
                "open": [10.0],
                "high": [11.0],
                "low": [9.0],
                "close": [10.5],
                "volume": [1000],
                "amount": [10_000],
            }
        )

    def test_defaults_and_validation(self) -> None:
        normalized = normalize_daily_frame(self.frame)
        self.assertIn("adj_factor", normalized)
        self.assertFalse(bool(normalized.loc[0, "is_st"]))
        report = validate_daily_frame(normalized)
        self.assertEqual(report.rows, 1)

    def test_duplicate_key_fails(self) -> None:
        duplicate = pd.concat([self.frame, self.frame], ignore_index=True)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            validate_daily_frame(duplicate)


if __name__ == "__main__":
    unittest.main()
