"""Optional LightGBM adapter; the NumPy Ridge baseline always remains runnable."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pandas as pd


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
        "subsample_freq": 1,
        "colsample_bytree": 0.8,
        "reg_alpha": 0.1,
        "reg_lambda": 1.0,
        "random_state": random_state,
        "n_jobs": -1,
    }
    parameters.update(overrides)
    return LGBMRegressor(**parameters)


def fit_lightgbm_model(
    train_x: pd.DataFrame,
    train_y: pd.Series,
    validation_x: pd.DataFrame,
    validation_y: pd.Series,
    *,
    random_state: int = 42,
    early_stopping_rounds: int = 50,
    **parameters: Any,
) -> Any:
    """Fit LightGBM with an explicit chronological validation set."""
    try:
        import lightgbm as lgb
    except ImportError as exc:
        raise RuntimeError(
            "LightGBM is optional. Install with: pip install -e .[ml]"
        ) from exc
    if early_stopping_rounds < 1:
        raise ValueError("early_stopping_rounds must be positive")
    model = make_lightgbm_model(random_state=random_state, **parameters)
    fit_parameters = {
        "eval_metric": "l2",
        "callbacks": [
            lgb.early_stopping(early_stopping_rounds, verbose=False),
            lgb.log_evaluation(period=0),
        ],
    }
    if "eval_X" in inspect.signature(model.fit).parameters:
        fit_parameters["eval_X"] = validation_x
        fit_parameters["eval_y"] = validation_y
    else:  # LightGBM before the eval_X/eval_y transition.
        fit_parameters["eval_set"] = [(validation_x, validation_y)]
    model.fit(train_x, train_y, **fit_parameters)
    return model


def save_lightgbm_model(
    model: Any,
    path: str | Path,
    *,
    num_iteration: int | None = None,
) -> Path:
    """Save through Python so non-ASCII Windows paths bypass the C file API."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    model_text = model.booster_.model_to_string(num_iteration=num_iteration)
    output.write_text(model_text, encoding="utf-8")
    return output
