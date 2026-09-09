import unittest

import numpy as np
import pandas as pd

from quant_lab.models.ridge import RidgeRegressor


class RidgeTest(unittest.TestCase):
    def test_learns_linear_relationship_with_missing_values(self) -> None:
        x = pd.DataFrame({"x1": np.arange(20.0), "x2": np.arange(20.0) ** 2})
        x.loc[3, "x2"] = np.nan
        y = 2 * x["x1"] + 1
        model = RidgeRegressor(alpha=0.01).fit(x, y)
        prediction = model.predict(pd.DataFrame({"x1": [5.0], "x2": [25.0]}))
        self.assertLess(abs(float(prediction[0]) - 11.0), 1.0)


if __name__ == "__main__":
    unittest.main()
