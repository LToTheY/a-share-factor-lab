"""Selective DuckDB reads for interactive strategy experiments."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from quant_lab.dashboard.artifacts import ArtifactError
from quant_lab.data.market_dataset import require_matching_dataset

MARKET_COLUMNS = [
    "trade_date",
    "symbol",
    "open",
    "close",
    "adj_close",
    "in_index",
    "is_suspended",
    "is_limit_up",
    "is_limit_down",
]


class StrategyDataStore:
    """Read only the selected period and columns from large parquet artifacts."""

    def __init__(self, score_file: str | Path, market_file: str | Path):
        self.score_file = Path(score_file).expanduser().resolve()
        self.market_file = Path(market_file).expanduser().resolve()

    def _require_files(self) -> None:
        for path in (self.score_file, self.market_file):
            if not path.is_file():
                raise ArtifactError(f"缺少策略数据文件：{path}")
        try:
            require_matching_dataset(self.score_file, self.market_file)
        except ValueError as exc:
            raise ArtifactError(str(exc)) from exc

    @staticmethod
    def _duckdb():
        try:
            import duckdb
        except ImportError as exc:  # pragma: no cover
            raise ArtifactError("策略实验室需要安装 duckdb") from exc
        return duckdb

    def date_bounds(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        self._require_files()
        duckdb = self._duckdb()
        row = duckdb.execute(
            "SELECT min(trade_date), max(trade_date) FROM read_parquet(?)",
            [str(self.score_file)],
        ).fetchone()
        if not row or row[0] is None or row[1] is None:
            raise ArtifactError("因子分数文件中没有可用日期")
        return pd.Timestamp(row[0]), pd.Timestamp(row[1])

    def load(
        self,
        factors: list[str],
        start_date: str | pd.Timestamp,
        end_date: str | pd.Timestamp,
        allowed_factors: list[str],
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        self._require_files()
        if not factors:
            raise ArtifactError("至少需要选择一个因子")
        unknown = sorted(set(factors).difference(allowed_factors))
        if unknown:
            raise ArtifactError(f"未知因子：{', '.join(unknown)}")
        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)
        if start >= end:
            raise ArtifactError("回测开始日期必须早于结束日期")
        duckdb = self._duckdb()
        factor_sql = ", ".join(f'"{name.replace(chr(34), chr(34) * 2)}"' for name in factors)
        score_query = f'''\
            SELECT trade_date, symbol, in_universe, {factor_sql}
            FROM read_parquet(?)
            WHERE trade_date BETWEEN ? AND ?
            ORDER BY trade_date, symbol
        '''
        available = {row[0] for row in duckdb.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(self.market_file)]).fetchall()}
        extra = [name for name in ["is_usable_market_data", "is_st_known", "is_suspended_known",
                                  "limit_status_known", "research_segment"] if name in available]
        provenance_path = self.score_file.parent / "dataset_provenance.json"
        pool_column = None
        if provenance_path.exists():
            from quant_lab.research.service import POOLS
            source = json.loads(provenance_path.read_text(encoding="utf-8"))
            pool_column = POOLS.get(source.get("universe"))
            if pool_column:
                if pool_column not in available:
                    raise ArtifactError("行情缺少研究股票池标记")
                extra.append(pool_column)
        market_sql = ", ".join(f'"{name}"' for name in MARKET_COLUMNS + extra)
        market_query = f'''\
            SELECT {market_sql}
            FROM read_parquet(?)
            WHERE trade_date BETWEEN ? AND ?
            ORDER BY trade_date, symbol
        '''
        try:
            scores = duckdb.execute(
                score_query, [str(self.score_file), start, end]
            ).df()
            market = duckdb.execute(
                market_query, [str(self.market_file), start, end]
            ).df()
        except Exception as exc:
            raise ArtifactError(f"读取策略回测数据失败：{exc}") from exc
        if scores.empty or market.empty:
            raise ArtifactError("所选日期区间没有足够的策略数据")
        scores["trade_date"] = pd.to_datetime(scores["trade_date"])
        market["trade_date"] = pd.to_datetime(market["trade_date"])
        if pool_column:
            market["in_index"] = market[pool_column].fillna(False)
        return scores, market
