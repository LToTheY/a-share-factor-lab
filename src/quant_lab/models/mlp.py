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
    random_state: int = 42
    model_: Any | None = None
    medians_: np.ndarray | None = None
    means_: np.ndarray | None = None
    scales_: np.ndarray | None = None

    def _torch(self) -> Any:
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError(
                "PyTorch is optional. Install with: pip install -e .[dl]"
            ) from exc
        return torch

    def fit(
        self, x: pd.DataFrame | np.ndarray, y: pd.Series | np.ndarray
    ) -> MLPRegressor:
        torch = self._torch()
        torch.manual_seed(self.random_state)
        matrix = np.asarray(x, dtype=np.float32)
        target = np.asarray(y, dtype=np.float32)
        valid = np.isfinite(target)
        matrix, target = matrix[valid], target[valid]
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
        )
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
        self.model_.train()
        for _ in range(self.epochs):
            for features, labels in loader:
                optimizer.zero_grad()
                loss = loss_function(self.model_(features), labels)
                loss.backward()
                optimizer.step()
        return self

    def predict(self, x: pd.DataFrame | np.ndarray) -> np.ndarray:
        torch = self._torch()
        if self.model_ is None:
            raise RuntimeError("Fit the model before prediction")
        matrix = np.asarray(x, dtype=np.float32)
        matrix = np.where(np.isfinite(matrix), matrix, self.medians_)
        matrix = (matrix - self.means_) / self.scales_
        self.model_.eval()
        with torch.no_grad():
            return self.model_(torch.tensor(matrix)).numpy().reshape(-1)
