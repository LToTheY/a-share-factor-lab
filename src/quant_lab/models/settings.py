"""Typed and validated configuration for machine-learning experiments."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quant_lab.utils.config import load_config

SUPPORTED_MODELS = {"ridge", "lightgbm", "mlp"}


@dataclass(frozen=True)
class MLSettings:
    output_dir: str
    scores_file: str
    market_file: str
    research_config: str
    feature_names: tuple[str, ...]
    label_col: str
    minimum_valid_features: int
    train_years: int
    validation_years: int
    test_years: int
    step_years: int
    embargo_trading_days: int
    enabled_models: tuple[str, ...]
    model_parameters: dict[str, dict[str, Any]]
    portfolio_variants: tuple[dict[str, Any], ...] = ()


def load_ml_settings(path: str | Path) -> MLSettings:
    config = load_config(path)
    project = config["project"]
    data = config["data"]
    features = config["features"]
    validation = config["validation"]
    models = config["models"]

    names = tuple(str(name) for name in features["names"])
    if not names or len(names) != len(set(names)):
        raise ValueError("ML feature names must be non-empty and unique")
    minimum_valid = int(features.get("minimum_valid_features", len(names)))
    if not 1 <= minimum_valid <= len(names):
        raise ValueError("minimum_valid_features must be within configured features")

    windows = {
        "train_years": int(validation["train_years"]),
        "validation_years": int(validation["validation_years"]),
        "test_years": int(validation.get("test_years", 1)),
        "step_years": int(validation.get("step_years", 1)),
    }
    if min(windows.values()) < 1:
        raise ValueError("All ML walk-forward year windows must be positive")
    if windows["step_years"] < windows["test_years"]:
        raise ValueError("step_years must not create overlapping test windows")
    embargo = int(validation.get("embargo_trading_days", 0))
    if embargo < 0:
        raise ValueError("embargo_trading_days cannot be negative")

    enabled = tuple(str(name).lower() for name in models.get("enabled", ["ridge"]))
    unknown = sorted(set(enabled) - SUPPORTED_MODELS)
    if unknown:
        raise ValueError(f"Unsupported ML models: {', '.join(unknown)}")
    if len(enabled) != len(set(enabled)) or not enabled:
        raise ValueError("Enabled ML models must be non-empty and unique")
    ridge_alphas = [float(value) for value in models.get("ridge", {}).get("alphas", [])]
    if "ridge" in enabled and (not ridge_alphas or min(ridge_alphas) < 0):
        raise ValueError("Ridge alphas must be a non-empty list of non-negative values")

    parameters = {
        name: dict(models.get(name, {})) for name in SUPPORTED_MODELS
    }
    variants = tuple(dict(item) for item in config.get("portfolio_sensitivity", []))
    variant_names = [str(item.get("name", "")) for item in variants]
    if any(not name for name in variant_names) or len(variant_names) != len(
        set(variant_names)
    ):
        raise ValueError("Portfolio sensitivity variants need unique non-empty names")
    for item in variants:
        top_n = int(item["top_n"])
        exit_rank = int(item["exit_rank"])
        max_weight = float(item["max_weight"])
        if top_n <= 0 or exit_rank < top_n or not 0 < max_weight <= 1:
            raise ValueError(f"Invalid portfolio sensitivity variant: {item['name']}")
    return MLSettings(
        output_dir=str(project["output_dir"]),
        scores_file=str(data["scores_file"]),
        market_file=str(data["market_file"]),
        research_config=str(data["research_config"]),
        feature_names=names,
        label_col=str(features["label_col"]),
        minimum_valid_features=minimum_valid,
        train_years=windows["train_years"],
        validation_years=windows["validation_years"],
        test_years=windows["test_years"],
        step_years=windows["step_years"],
        embargo_trading_days=embargo,
        enabled_models=enabled,
        model_parameters=parameters,
        portfolio_variants=variants,
    )
