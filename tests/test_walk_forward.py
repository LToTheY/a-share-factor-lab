import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from quant_lab.research.settings import FactorDefinition, ResearchSettings
from quant_lab.research.walk_forward import run_walk_forward


class WalkForwardTest(unittest.TestCase):
    def test_selection_uses_past_windows_and_scores_future_year(self) -> None:
        dates = pd.bdate_range("2016-01-01", "2023-12-31")
        symbols = [f"S{index:02d}" for index in range(12)]
        index = pd.MultiIndex.from_product(
            [dates, symbols], names=["trade_date", "symbol"]
        )
        scores = index.to_frame(index=False)
        cross_section = scores["symbol"].str[1:].astype(float)
        scores["factor_good"] = cross_section
        scores["factor_bad"] = -cross_section
        scores["forward_return_5d"] = cross_section / 100.0
        scores["in_universe"] = True
        scores["close"] = 10.0
        market = scores[["trade_date", "symbol"]].copy()
        market["open"] = 10.0
        market["close"] = 10.0
        settings = ResearchSettings(
            raw_dir="raw",
            processed_file="processed.parquet",
            output_dir="output",
            initial_calendar_days=800,
            max_stale_trading_days=1,
            required_latest_coverage=0.98,
            min_listed_days=0,
            min_amount=0.0,
            require_known_status=False,
            forward_periods=5,
            winsor_n_mad=5.0,
            neutralize_size=False,
            neutralize_industry=False,
            groups=5,
            factors=(
                FactorDefinition("factor_good", 1.0),
                FactorDefinition("factor_bad", 1.0),
            ),
            rebalance_frequency="W-FRI",
            top_n=3,
            exit_rank=4,
            max_weight=1 / 3,
            backtest={
                "initial_cash": 100_000,
                "commission_rate": 0,
                "stamp_duty_rate": 0,
                "historical_stamp_duty_rate": 0,
                "transfer_fee_rate": 0,
                "slippage_bps": 0,
                "minimum_commission": 0,
                "lot_size": 100,
            },
            paper_state_file="state.json",
            next_orders_file="orders.csv",
            research_start_date="2016-01-01",
            minimum_valid_factors=1,
            validation={
                "train_years": 2,
                "validation_years": 1,
                "embargo_trading_days": 5,
            },
        )
        with tempfile.TemporaryDirectory() as temporary:
            summary = run_walk_forward(
                scores,
                market,
                ["factor_good", "factor_bad"],
                "forward_return_5d",
                settings,
                Path(temporary),
            )
            folds = pd.read_csv(Path(temporary) / "fold_summary.csv")
        self.assertEqual(summary["status"], "OK")
        self.assertTrue((folds["selected_factors"] == "factor_good").all())
        self.assertGreater(summary["ic"]["mean_ic"], 0.9)
        self.assertTrue(np.isfinite(summary["portfolio"]["total_return"]))


if __name__ == "__main__":
    unittest.main()
