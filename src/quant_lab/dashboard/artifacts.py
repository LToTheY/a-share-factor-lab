"""Validated, read-only access to generated research artifacts."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd


class ArtifactError(RuntimeError):
    """Raised when a dashboard artifact is absent or has an invalid schema."""


CSV_SCHEMAS: dict[str, set[str]] = {
    "factor_summary.csv": {"factor", "mean_ic", "icir", "win_rate", "observations"},
    "factor_coverage.csv": {"trade_date", "factor", "coverage"},
    "factor_stability.csv": {"factor", "window", "mean_ic", "win_rate"},
    "latest_signal.csv": {"trade_date", "symbol", "factor_processed", "factor_rank"},
    "equity.csv": {"trade_date", "equity"},
    "benchmark_equity.csv": {"trade_date", "equity"},
    "trades.csv": {"trade_date", "symbol", "side", "shares", "price"},
    "positions.csv": {"trade_date", "symbol", "shares", "market_value"},
    "walk_forward/fold_summary.csv": {
        "test_year",
        "selected_factors",
        "selected_count",
        "test_mean_ic",
    },
    "walk_forward/oos_ic.csv": {"trade_date", "rank_ic"},
    "walk_forward/equity.csv": {"trade_date", "equity"},
    "walk_forward/benchmark_equity.csv": {"trade_date", "equity"},
}

DATE_COLUMNS = {"trade_date", "signal_date", "start_date", "end_date"}


def _stamp(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return str(path), stat.st_mtime_ns, stat.st_size


@lru_cache(maxsize=64)
def _read_json_cached(path: str, _mtime: int, _size: int) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ArtifactError(f"JSON 顶层必须是对象：{path}")
    return value


@lru_cache(maxsize=128)
def _read_csv_cached(path: str, _mtime: int, _size: int) -> pd.DataFrame:
    return pd.read_csv(path)


class ArtifactStore:
    """Load generated files without changing research or paper-account state."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()

    def _path(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if self.root != path and self.root not in path.parents:
            raise ArtifactError(f"禁止读取报告目录以外的路径：{relative}")
        return path

    def exists(self, relative: str) -> bool:
        return self._path(relative).is_file()

    def json(self, relative: str) -> dict[str, Any]:
        path = self._path(relative)
        if not path.is_file():
            raise ArtifactError(f"缺少研究文件：{path}")
        return dict(_read_json_cached(*_stamp(path)))

    def csv(self, relative: str, *, required: set[str] | None = None) -> pd.DataFrame:
        path = self._path(relative)
        if not path.is_file():
            raise ArtifactError(f"缺少研究文件：{path}")
        frame = _read_csv_cached(*_stamp(path)).copy()
        expected = required if required is not None else CSV_SCHEMAS.get(relative, set())
        missing = sorted(expected.difference(frame.columns))
        if missing:
            raise ArtifactError(f"{relative} 缺少字段：{', '.join(missing)}")
        for column in DATE_COLUMNS.intersection(frame.columns):
            frame[column] = pd.to_datetime(frame[column], errors="coerce")
        return frame

    def factor_names(self) -> list[str]:
        summary = self.csv("factor_summary.csv")
        return summary["factor"].dropna().astype(str).tolist()

    def factor_ic(self, factor: str) -> pd.DataFrame:
        if factor not in self.factor_names():
            raise ArtifactError(f"未知因子：{factor}")
        return self.csv(
            f"ic_{factor}.csv", required={"trade_date", "rank_ic"}
        )

    def factor_snapshot(self, factor: str) -> pd.DataFrame:
        """Read only one factor and the latest date from the large parquet file."""
        if factor not in self.factor_names():
            raise ArtifactError(f"未知因子：{factor}")
        path = self._path("factor_scores.parquet")
        if not path.is_file():
            raise ArtifactError(f"缺少研究文件：{path}")
        try:
            import duckdb
        except ImportError as exc:  # pragma: no cover - dependency error path
            raise ArtifactError("读取大型因子文件需要安装 duckdb") from exc
        quoted = factor.replace('"', '""')
        query = f'''\
            SELECT trade_date, symbol, "{quoted}" AS factor_value,
                   factor_processed, close, in_universe
            FROM read_parquet(?)
            WHERE trade_date = (SELECT max(trade_date) FROM read_parquet(?))
              AND "{quoted}" IS NOT NULL
            ORDER BY "{quoted}" DESC
        '''
        try:
            frame = duckdb.execute(query, [str(path), str(path)]).df()
        except Exception as exc:
            raise ArtifactError(f"读取 factor_scores.parquet 失败：{exc}") from exc
        if "trade_date" in frame:
            frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
        return frame

    def correlation(self) -> pd.DataFrame:
        frame = self.csv("factor_correlation.csv", required=set())
        first = frame.columns[0]
        if first.startswith("Unnamed"):
            frame = frame.rename(columns={first: "factor"})
        if "factor" not in frame.columns:
            raise ArtifactError("factor_correlation.csv 缺少行标签")
        return frame.set_index("factor")

    def health(self) -> list[dict[str, str]]:
        required = [
            "summary.json",
            "run_status.json",
            "factor_summary.csv",
            "factor_scores.parquet",
            "equity.csv",
            "benchmark_equity.csv",
            "walk_forward/summary.json",
        ]
        return [
            {
                "文件": name,
                "状态": "正常" if self.exists(name) else "缺失",
            }
            for name in required
        ]
