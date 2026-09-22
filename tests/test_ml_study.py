import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from quant_lab.models.settings import MLSettings
from quant_lab.research.ml_study import run_ml_study
from quant_lab.research.settings import FactorDefinition, ResearchSettings


class MLStudyTest(unittest.TestCase):
    def test_ridge_walk_forward_writes_oos_artifacts(self) -> None:
        dates = pd.bdate_range("2016-01-01", "2023-12-31")
        symbols = [f"S{index:02d}" for index in range(12)]
        index = pd.MultiIndex.from_product(
            [dates, symbols], names=["trade_date", "symbol"]
        )
        scores = index.to_frame(index=False)
        cross_section = scores["symbol"].str[1:].astype(float)
        time_signal = np.sin(np.arange(len(scores)) / 100.0)
        scores["factor_good"] = cross_section + time_signal * 0.01
        scores["factor_noise"] = time_signal
        scores["forward_return_5d"] = cross_section / 100.0
        scores["in_universe"] = True

        market = scores[["trade_date", "symbol", "in_universe"]].copy()
        market["in_index"] = True
        market["open"] = 10.0
        market["close"] = 10.0
        market["adj_close"] = 10.0

        ml_settings = MLSettings(
            output_dir="output",
            scores_file="scores.parquet",
            market_file="market.parquet",
            research_config="research.yaml",
            feature_names=("factor_good", "factor_noise"),
            label_col="forward_return_5d",
            minimum_valid_features=1,
            train_years=2,
            validation_years=1,
            test_years=1,
            step_years=1,
            embargo_trading_days=5,
            enabled_models=("ridge",),
            model_parameters={
                "ridge": {"alphas": [0.1, 1.0]},
                "lightgbm": {},
                "mlp": {},
            },
            portfolio_variants=(
                {
                    "name": "monthly",
                    "frequency": "M",
                    "top_n": 3,
                    "exit_rank": 4,
                    "max_weight": 1 / 3,
                },
            ),
        )
        research_settings = ResearchSettings(
            raw_dir="raw",
            processed_file="processed.parquet",
            output_dir="factor_output",
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
            factors=(FactorDefinition("factor_good", 1.0),),
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
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            summary = run_ml_study(
                scores,
                market,
                ml_settings,
                research_settings,
                output,
            )
            folds = pd.read_csv(output / "fold_summary.csv")
            predictions = pd.read_parquet(output / "oos_predictions.parquet")

            self.assertEqual(summary["status"], "OK")
            self.assertIn("ridge", summary["models"])
            self.assertIn("annual_cost_drag", summary["models"]["ridge"])
            self.assertGreater(summary["models"]["ridge"]["ic"]["mean_ic"], 0.9)
            self.assertEqual(int(folds["test_year"].min()), 2019)
            self.assertIn("ridge_prediction", predictions)
            self.assertTrue((output / "feature_importance.csv").exists())
            self.assertTrue((output / "feature_stability.csv").exists())
            self.assertTrue((output / "portfolio_sensitivity.csv").exists())
            self.assertTrue((output / "ridge" / "equity.csv").exists())
            self.assertTrue((output / "ridge" / "equity_no_cost.csv").exists())
            self.assertTrue((output / "fold_models" / "ridge_2019.json").exists())
            self.assertTrue((output / "REPORT.md").exists())


if __name__ == "__main__":
    unittest.main()
