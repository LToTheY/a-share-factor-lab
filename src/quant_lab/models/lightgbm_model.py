"""Optional LightGBM adapter; the NumPy Ridge baseline always remains runnable."""

from __future__ import annotations

from typing import Any


def make_lightgbm_model(random_state: int = 42, **overrides: Any) -> Any:
    try:
        from lightgbm import LGBMRegressor
    except ImportError as exc:
        raise RuntimeError(
            "LightGBM is optional. Install with: pip install -e .[ml]"
        ) from exc
    parameters = {
        "n_estimators": 300,
        "learning_rate": 0.03,
        "num_leaves": 31,
        "max_depth": -1,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "reg_alpha": 0.1,
        "reg_lambda": 1.0,
        "random_state": random_state,
        "n_jobs": -1,
    }
    parameters.update(overrides)
    return LGBMRegressor(**parameters)
