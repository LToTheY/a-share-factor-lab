import importlib.util
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from quant_lab.models.lightgbm_model import fit_lightgbm_model, save_lightgbm_model
from quant_lab.models.mlp import MLPRegressor
from quant_lab.models.ridge import RidgeRegressor


class RidgeTest(unittest.TestCase):
    def test_learns_linear_relationship_with_missing_values(self) -> None:
        x = pd.DataFrame({"x1": np.arange(20.0), "x2": np.arange(20.0) ** 2})
        x.loc[3, "x2"] = np.nan
        y = 2 * x["x1"] + 1
        model = RidgeRegressor(alpha=0.01).fit(x, y)
        prediction = model.predict(pd.DataFrame({"x1": [5.0], "x2": [25.0]}))
        self.assertLess(abs(float(prediction[0]) - 11.0), 1.0)

    def test_rejects_negative_alpha_and_feature_order_changes(self) -> None:
        x = pd.DataFrame({"x1": [1.0, 2.0], "x2": [3.0, 4.0]})
        y = pd.Series([0.0, 1.0])
        with self.assertRaisesRegex(ValueError, "alpha"):
            RidgeRegressor(alpha=-1.0).fit(x, y)
        model = RidgeRegressor().fit(x, y)
        with self.assertRaisesRegex(ValueError, "column order"):
            model.predict(x[["x2", "x1"]])


@unittest.skipUnless(importlib.util.find_spec("lightgbm"), "LightGBM is optional")
class LightGBMTest(unittest.TestCase):
    def test_fits_with_chronological_validation_data(self) -> None:
        train_x = pd.DataFrame({"feature": np.arange(40.0)})
        train_y = pd.Series(np.arange(40.0) / 40.0)
        validation_x = pd.DataFrame({"feature": np.arange(40.0, 50.0)})
        validation_y = pd.Series(np.arange(40.0, 50.0) / 40.0)
        model = fit_lightgbm_model(
            train_x,
            train_y,
            validation_x,
            validation_y,
            n_estimators=20,
            min_child_samples=2,
            verbosity=-1,
            early_stopping_rounds=3,
        )
        prediction = model.predict(validation_x)
        self.assertEqual(len(prediction), len(validation_x))
        self.assertGreater(model.best_iteration_, 0)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "中文目录" / "model.txt"
            save_lightgbm_model(
                model, output, num_iteration=model.best_iteration_
            )
            self.assertIn("tree", output.read_text(encoding="utf-8"))


@unittest.skipUnless(importlib.util.find_spec("torch"), "PyTorch is optional")
class MLPTest(unittest.TestCase):
    def test_uses_validation_early_stopping_and_restores_best_state(self) -> None:
        train_x = pd.DataFrame({"feature": np.linspace(-1.0, 1.0, 40)})
        train_y = pd.Series(train_x["feature"] * 0.5)
        validation_x = pd.DataFrame({"feature": np.linspace(-0.8, 0.8, 12)})
        validation_y = pd.Series(validation_x["feature"] * 0.5)
        model = MLPRegressor(
            hidden_size=8,
            epochs=5,
            batch_size=16,
            patience=2,
            random_state=7,
        ).fit(
            train_x,
            train_y,
            validation_data=(validation_x, validation_y),
        )
        prediction = model.predict(validation_x)
        self.assertEqual(len(prediction), len(validation_x))
        self.assertIsNotNone(model.best_epoch_)
        self.assertTrue(model.training_history_)


if __name__ == "__main__":
    unittest.main()
