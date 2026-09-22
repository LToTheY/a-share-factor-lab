"""Optional PyTorch MLP baseline for tabular cross-sectional factors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class MLPRegressor:
    hidden_size: int = 64
    learning_rate: float = 1e-3
    epochs: int = 30
    batch_size: int = 1024
    patience: int = 5
    random_state: int = 42
    device: str = "cpu"
    deterministic: bool = True
    model_: Any | None = None
    medians_: np.ndarray | None = None
    means_: np.ndarray | None = None
    scales_: np.ndarray | None = None
    best_epoch_: int | None = None
    training_history_: list[dict[str, float]] | None = None
    feature_names_: tuple[str, ...] | None = None

    def _torch(self) -> Any:
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError(
                "PyTorch is optional. Install with: pip install -e .[dl]"
            ) from exc
        return torch

    def fit(
        self,
        x: pd.DataFrame | np.ndarray,
        y: pd.Series | np.ndarray,
        validation_data: tuple[
            pd.DataFrame | np.ndarray, pd.Series | np.ndarray
        ]
        | None = None,
    ) -> MLPRegressor:
        torch = self._torch()
        if self.hidden_size < 2 or self.epochs < 1 or self.batch_size < 1:
            raise ValueError("hidden_size, epochs and batch_size must be positive")
        if self.learning_rate <= 0 or self.patience < 1:
            raise ValueError("learning_rate and patience must be positive")
        torch.manual_seed(self.random_state)
        if self.deterministic:
            torch.use_deterministic_algorithms(True)
        if isinstance(x, pd.DataFrame):
            self.feature_names_ = tuple(str(column) for column in x.columns)
        matrix = np.asarray(x, dtype=np.float32)
        target = np.asarray(y, dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[1] == 0:
            raise ValueError("Training features must be a non-empty 2D matrix")
        if target.ndim != 1 or len(target) != len(matrix):
            raise ValueError("Target must be one-dimensional and align with features")
        valid = np.isfinite(target)
        matrix, target = matrix[valid], target[valid]
        if len(target) == 0:
            raise ValueError("No finite training targets")
        self.medians_ = np.nanmedian(matrix, axis=0)
        self.medians_ = np.where(np.isfinite(self.medians_), self.medians_, 0.0)
        matrix = np.where(np.isfinite(matrix), matrix, self.medians_)
        self.means_ = matrix.mean(axis=0)
        self.scales_ = matrix.std(axis=0)
        self.scales_ = np.where(self.scales_ > 0, self.scales_, 1.0)
        matrix = (matrix - self.means_) / self.scales_

        self.model_ = torch.nn.Sequential(
            torch.nn.Linear(matrix.shape[1], self.hidden_size),
            torch.nn.ReLU(),
            torch.nn.Dropout(0.1),
            torch.nn.Linear(self.hidden_size, self.hidden_size // 2),
            torch.nn.ReLU(),
            torch.nn.Linear(self.hidden_size // 2, 1),
        ).to(self.device)
        optimizer = torch.optim.AdamW(
            self.model_.parameters(), lr=self.learning_rate, weight_decay=1e-4
        )
        loss_function = torch.nn.MSELoss()
        dataset = torch.utils.data.TensorDataset(
            torch.tensor(matrix), torch.tensor(target).reshape(-1, 1)
        )
        generator = torch.Generator().manual_seed(self.random_state)
        loader = torch.utils.data.DataLoader(
            dataset, batch_size=self.batch_size, shuffle=True, generator=generator
        )
        validation_tensors = None
        if validation_data is not None:
            validation_x, validation_y = validation_data
            if (
                isinstance(validation_x, pd.DataFrame)
                and self.feature_names_ is not None
                and tuple(str(column) for column in validation_x.columns)
                != self.feature_names_
            ):
                raise ValueError("Validation features must match training column order")
            validation_matrix = np.asarray(validation_x, dtype=np.float32)
            validation_target = np.asarray(validation_y, dtype=np.float32)
            valid_validation = np.isfinite(validation_target)
            validation_matrix = validation_matrix[valid_validation]
            validation_target = validation_target[valid_validation]
            if validation_matrix.ndim != 2 or len(validation_target) == 0:
                raise ValueError("Validation data must contain finite targets")
            validation_matrix = np.where(
                np.isfinite(validation_matrix), validation_matrix, self.medians_
            )
            validation_matrix = (validation_matrix - self.means_) / self.scales_
            validation_tensors = (
                torch.tensor(validation_matrix, device=self.device),
                torch.tensor(validation_target, device=self.device).reshape(-1, 1),
            )

        best_loss = float("inf")
        best_state = None
        stale_epochs = 0
        self.training_history_ = []
        for epoch in range(self.epochs):
            self.model_.train()
            training_loss = 0.0
            training_rows = 0
            for features, labels in loader:
                features = features.to(self.device)
                labels = labels.to(self.device)
                optimizer.zero_grad()
                loss = loss_function(self.model_(features), labels)
                loss.backward()
                optimizer.step()
                training_loss += float(loss.detach().cpu()) * len(features)
                training_rows += len(features)
            mean_training_loss = training_loss / training_rows
            validation_loss = mean_training_loss
            if validation_tensors is not None:
                self.model_.eval()
                with torch.no_grad():
                    validation_loss = float(
                        loss_function(
                            self.model_(validation_tensors[0]), validation_tensors[1]
                        ).detach().cpu()
                    )
            self.training_history_.append(
                {
                    "epoch": float(epoch + 1),
                    "train_loss": mean_training_loss,
                    "validation_loss": validation_loss,
                }
            )
            if validation_loss < best_loss - 1e-12:
                best_loss = validation_loss
                self.best_epoch_ = epoch + 1
                best_state = {
                    name: value.detach().cpu().clone()
                    for name, value in self.model_.state_dict().items()
                }
                stale_epochs = 0
            else:
                stale_epochs += 1
                if validation_tensors is not None and stale_epochs >= self.patience:
                    break
        if best_state is not None:
            self.model_.load_state_dict(best_state)
        return self

    def predict(self, x: pd.DataFrame | np.ndarray) -> np.ndarray:
        torch = self._torch()
        if self.model_ is None:
            raise RuntimeError("Fit the model before prediction")
        if any(value is None for value in [self.medians_, self.means_, self.scales_]):
            raise RuntimeError("Fit preprocessing state is incomplete")
        if (
            isinstance(x, pd.DataFrame)
            and self.feature_names_ is not None
            and tuple(str(column) for column in x.columns) != self.feature_names_
        ):
            raise ValueError("Prediction features must match training column order")
        matrix = np.asarray(x, dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[1] != len(self.medians_):
            raise ValueError("Prediction feature shape does not match fitted model")
        matrix = np.where(np.isfinite(matrix), matrix, self.medians_)
        matrix = (matrix - self.means_) / self.scales_
        self.model_.eval()
        with torch.no_grad():
            prediction = self.model_(torch.tensor(matrix, device=self.device))
            return prediction.detach().cpu().numpy().reshape(-1)
