"""Small NumPy Ridge baseline with train-only imputation and scaling."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class RidgeRegressor:
    alpha: float = 1.0
    coefficients_: np.ndarray | None = None
    intercept_: float = 0.0
    medians_: np.ndarray | None = None
    means_: np.ndarray | None = None
    scales_: np.ndarray | None = None
    feature_names_: tuple[str, ...] | None = None

    def fit(
        self, x: pd.DataFrame | np.ndarray, y: pd.Series | np.ndarray
    ) -> RidgeRegressor:
        if self.alpha < 0:
            raise ValueError("alpha cannot be negative")
        if isinstance(x, pd.DataFrame):
            if x.columns.duplicated().any():
                raise ValueError("Training features must have unique names")
            self.feature_names_ = tuple(str(column) for column in x.columns)
        matrix = np.asarray(x, dtype=float)
        target = np.asarray(y, dtype=float)
        if matrix.ndim != 2 or matrix.shape[1] == 0:
            raise ValueError("Training features must be a non-empty 2D matrix")
        if target.ndim != 1 or len(target) != len(matrix):
            raise ValueError("Target must be one-dimensional and align with features")
        valid_y = np.isfinite(target)
        matrix = matrix[valid_y]
        target = target[valid_y]
        if len(target) == 0:
            raise ValueError("No finite training targets")

        self.medians_ = np.nanmedian(matrix, axis=0)
        self.medians_ = np.where(np.isfinite(self.medians_), self.medians_, 0.0)
        matrix = np.where(np.isfinite(matrix), matrix, self.medians_)
        self.means_ = matrix.mean(axis=0)
        self.scales_ = matrix.std(axis=0)
        self.scales_ = np.where(self.scales_ > 0, self.scales_, 1.0)
        standardized = (matrix - self.means_) / self.scales_
        self.intercept_ = float(target.mean())
        centered_target = target - self.intercept_
        gram = standardized.T @ standardized
        penalty = self.alpha * np.eye(gram.shape[0])
        self.coefficients_ = np.linalg.solve(
            gram + penalty, standardized.T @ centered_target
        )
        return self

    def predict(self, x: pd.DataFrame | np.ndarray) -> np.ndarray:
        if any(
            value is None
            for value in [self.coefficients_, self.medians_, self.means_, self.scales_]
        ):
            raise RuntimeError("Fit the model before prediction")
        if isinstance(x, pd.DataFrame) and self.feature_names_ is not None:
            received = tuple(str(column) for column in x.columns)
            if received != self.feature_names_:
                raise ValueError("Prediction features must match training column order")
        matrix = np.asarray(x, dtype=float)
        if matrix.ndim != 2 or matrix.shape[1] != len(self.coefficients_):
            raise ValueError("Prediction feature shape does not match fitted model")
        matrix = np.where(np.isfinite(matrix), matrix, self.medians_)
        standardized = (matrix - self.means_) / self.scales_
        return self.intercept_ + standardized @ self.coefficients_


def prediction_rank_ic(
    frame: pd.DataFrame,
    prediction_col: str = "prediction",
    label_col: str = "label",
) -> pd.Series:
    def daily_ic(group: pd.DataFrame) -> float:
        valid = group[[prediction_col, label_col]].dropna()
        if len(valid) < 5:
            return np.nan
        prediction_rank = valid[prediction_col].rank(method="average")
        label_rank = valid[label_col].rank(method="average")
        return float(prediction_rank.corr(label_rank, method="pearson"))

    result = frame.groupby("trade_date").apply(daily_ic, include_groups=False)
    result.name = "prediction_rank_ic"
    return result
