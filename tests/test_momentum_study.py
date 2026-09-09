import tempfile
import unittest
from pathlib import Path

from quant_lab.data.panel import make_adjusted_market
from quant_lab.data.synthetic import make_synthetic_daily_data
from quant_lab.research.momentum import _prepare_momentum, run_momentum_study
from quant_lab.universe.filters import UniverseConfig, apply_universe


class MomentumStudyTest(unittest.TestCase):
    def test_forward_label_matches_each_symbol_key(self) -> None:
        market = make_synthetic_daily_data(10, 180, seed=21)
        market = apply_universe(
            make_adjusted_market(market), UniverseConfig(min_listed_days=1)
        )
        research = _prepare_momentum(market, 60, 5, horizons=(5,))
        sample = research.set_index(["symbol", "trade_date"])
        symbol = market["symbol"].iloc[0]
        symbol_market = market[market["symbol"] == symbol].sort_values("trade_date")
        row = symbol_market.iloc[100]
        expected = symbol_market.iloc[105]["close"] / row["close"] - 1.0
        actual = sample.at[(symbol, row["trade_date"]), "forward_return_5d"]
        self.assertAlmostEqual(actual, expected)

    def test_complete_report_is_reproducible(self) -> None:
        market = make_synthetic_daily_data(20, 260, seed=13)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            summary = run_momentum_study(market, output, top_n=10)
            self.assertTrue(summary["audit_passed"])
            for name in [
                "REPORT.md",
                "summary.json",
                "rank_ic.csv",
                "ic_decay.csv",
                "robustness.csv",
                "equity.csv",
                "trades.csv",
                "rank_ic.svg",
            ]:
                self.assertTrue((output / name).exists(), name)


if __name__ == "__main__":
    unittest.main()
