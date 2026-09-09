import tempfile
import unittest
from pathlib import Path

from quant_lab.pipeline import run_demo


class PipelineTest(unittest.TestCase):
    def test_demo_writes_end_to_end_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            summary = run_demo(output, seed=7, n_symbols=20, n_days=260)
            self.assertIn("factor", summary)
            self.assertTrue((output / "summary.json").exists())
            self.assertTrue((output / "equity.csv").exists())
            self.assertTrue((output / "model_predictions.csv").exists())


if __name__ == "__main__":
    unittest.main()
