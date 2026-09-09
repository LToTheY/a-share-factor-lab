import tempfile
import unittest
from pathlib import Path

import pandas as pd

from quant_lab.data.synthetic import make_synthetic_daily_data
from quant_lab.research.factor_suite import run_factor_suite
from quant_lab.research.settings import FactorDefinition, ResearchSettings


class FactorSuiteTest(unittest.TestCase):
    def test_configured_top_n_and_factor_outputs(self) -> None:
        market = make_synthetic_daily_data(12, 180, seed=7)
        settings = ResearchSettings(
            raw_dir="raw",
            processed_file="processed.parquet",
            output_dir="report",
            initial_calendar_days=800,
            max_stale_trading_days=1,
            required_latest_coverage=1.0,
            min_listed_days=20,
            min_amount=0.0,
            require_known_status=True,
            forward_periods=5,
            winsor_n_mad=5.0,
            neutralize_size=False,
            neutralize_industry=False,
            groups=5,
            factors=(
                FactorDefinition("momentum_20_5", 1.0),
                FactorDefinition("reversal_5", 1.0),
            ),
            rebalance_frequency="D",
            top_n=5,
            exit_rank=7,
            max_weight=0.2,
            backtest={
                "initial_cash": 1_000_000.0,
                "commission_rate": 0.0003,
                "stamp_duty_rate": 0.0005,
                "transfer_fee_rate": 0.00001,
                "slippage_bps": 5.0,
                "minimum_commission": 5.0,
                "lot_size": 100,
            },
            paper_state_file="state.json",
            next_orders_file="orders.csv",
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            summary, latest, targets = run_factor_suite(
                market,
                settings,
                output,
                next_trading_date=market["trade_date"].max() + pd.Timedelta(days=1),
            )
            self.assertEqual(summary["top_n"], 5)
            self.assertEqual(summary["factor_count"], 2)
            self.assertFalse(latest.empty)
            self.assertLessEqual(targets.groupby("trade_date").size().max(), 5)
            self.assertTrue((output / "factor_summary.csv").exists())
            self.assertTrue((output / "factor_correlation.csv").exists())


if __name__ == "__main__":
    unittest.main()
