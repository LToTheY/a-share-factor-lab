"""Immutable local experiment snapshots and dated ledger replay."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd

from quant_lab.dashboard.artifacts import ArtifactError

TABLES = ("equity", "trades", "positions", "targets", "benchmark", "execution_issues")


def frame_fingerprint(frame: pd.DataFrame) -> str:
    digest = hashlib.sha256()
    digest.update(str(list(zip(frame.columns, frame.dtypes.astype(str)))).encode())
    digest.update(pd.util.hash_pandas_object(frame, index=False).values.tobytes())
    return digest.hexdigest()


class ExperimentStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def _path(self, identifier: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise ArtifactError("无效实验编号")
        return self.root / identifier

    def save(self, result, spec, config, provenance: dict, name: str) -> str:
        identifier = uuid4().hex
        self.root.mkdir(parents=True, exist_ok=True)
        # Publish only after all tables and metadata have been written successfully.
        pending = self.root / f".pending-{identifier}"
        pending.mkdir()
        frames = {
            "equity": result.backtest.equity,
            "trades": result.backtest.trades,
            "positions": result.backtest.positions,
            "targets": result.targets,
            "benchmark": result.benchmark,
            "execution_issues": result.backtest.execution_issues,
        }
        for key, frame in frames.items():
            frame.to_parquet(pending / f"{key}.parquet", index=False)
        metadata = {
            "version": 1,
            "id": identifier,
            "name": name.strip() or "未命名实验",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "strategy": asdict(spec),
            "backtest": asdict(config),
            "provenance": provenance,
            "metrics": result.metrics,
            "benchmark_metrics": result.benchmark_metrics,
            "diagnostics": result.diagnostics,
            "actual_start": str(result.backtest.equity.trade_date.min().date()),
            "actual_end": str(result.backtest.equity.trade_date.max().date()),
        }
        (pending / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        pending.rename(self._path(identifier))
        return identifier

    def list(self) -> tuple[list[dict], list[str]]:
        records, errors = [], []
        if not self.root.exists():
            return records, errors
        for path in self.root.iterdir():
            if not re.fullmatch(r"[0-9a-f]{32}", path.name):
                continue
            try:
                records.append(self.metadata(path.name))
            except ArtifactError as exc:
                errors.append(str(exc))
        return sorted(
            records, key=lambda item: item["created_at"], reverse=True
        ), errors

    def notes(self, identifier: str) -> dict:
        path = self._path(identifier) / "notes.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def save_notes(self, identifier: str, conclusion: str) -> None:
        self.metadata(identifier)
        from quant_lab.research.jobs import atomic_json
        atomic_json(self._path(identifier) / "notes.json", {"conclusion": conclusion,
            "updated_at": datetime.now(timezone.utc).isoformat()})

    def metadata(self, identifier: str) -> dict:
        try:
            metadata = json.loads(
                (self._path(identifier) / "metadata.json").read_text(encoding="utf-8")
            )
            required = {
                "id",
                "name",
                "created_at",
                "strategy",
                "backtest",
                "provenance",
                "metrics",
                "actual_start",
                "actual_end",
            }
            if (
                not isinstance(metadata, dict)
                or not required.issubset(metadata)
                or metadata["id"] != identifier
            ):
                raise ValueError("元数据字段不完整或编号不匹配")
            return metadata
        except (OSError, ValueError) as exc:
            raise ArtifactError(f"实验 {identifier} 读取失败：{exc}") from exc

    def table(self, identifier: str, name: str) -> pd.DataFrame:
        if name not in TABLES:
            raise ArtifactError("未知实验数据表")
        try:
            return pd.read_parquet(self._path(identifier) / f"{name}.parquet")
        except (OSError, ValueError) as exc:
            raise ArtifactError(f"实验数据读取失败：{exc}") from exc


def replay_day(equity, targets, trades, positions, issues, date) -> dict:
    """Keep signal day, scheduled execution day and closing positions distinct."""
    date = pd.Timestamp(date)
    dates = pd.DatetimeIndex(pd.to_datetime(equity.trade_date).sort_values().unique())
    if date not in dates:
        raise ValueError("所选日期不在回测交易日中")
    signal = None
    if "trade_date" in targets:
        for candidate in sorted(pd.to_datetime(targets.trade_date).dropna().unique()):
            index = dates.searchsorted(candidate, side="right")
            if index < len(dates) and dates[index] == date:
                signal = pd.Timestamp(candidate)

    def on_day(frame, selected):
        if selected is None or "trade_date" not in frame:
            return frame.iloc[:0]
        return frame.loc[pd.to_datetime(frame.trade_date) == selected]

    return {
        "signal_date": signal,
        "equity": on_day(equity, date),
        "targets": on_day(targets, signal),
        "trades": on_day(trades, date),
        "positions": on_day(positions, date),
        "issues": on_day(issues, date),
    }
