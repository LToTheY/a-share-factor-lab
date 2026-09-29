"""Local factor metadata, independent of executable research configuration."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

FREQUENCIES = {
    "daily": "日频",
    "intraday": "日内",
    "other": "其他频率",
}
INTERVALS = {
    "daily": ["1d"],
    "intraday": ["1m", "5m", "15m", "30m", "60m", "tick"],
    "other": ["1w", "1mo", "event"],
}
CATEGORIES = ["动量", "反转", "波动率", "流动性", "量价", "基本面", "其他"]
STAGES = ["待研究", "开发中", "待验证", "已归档"]


def category_for(name: str) -> str:
    for prefix, category in (
        ("momentum", "动量"),
        ("reversal", "反转"),
        ("volatility", "波动率"),
        ("ivol", "波动率"),
        ("downside", "波动率"),
        ("overnight_reversal", "反转"),
        ("intraday_momentum", "动量"),
        ("amihud", "流动性"),
        ("turnover", "流动性"),
        ("book", "基本面"),
        ("earnings", "基本面"),
    ):
        if name.startswith(prefix):
            return category
    return "量价"


class FactorCatalog:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def records(self) -> list[dict]:
        if not self.path.exists():
            return []
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM factors ORDER BY updated_at DESC, name"
                )
            ]

    def save(self, record: dict, *, replace: bool = False) -> None:
        fields = (
            "frequency",
            "interval",
            "name",
            "title",
            "category",
            "stage",
            "definition",
            "notes",
        )
        values = {key: str(record.get(key, "")).strip() for key in fields}
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", values["name"]):
            raise ValueError(
                "因子标识须以小写字母开头，仅含小写字母、数字和下划线，最多80字符。"
            )
        if values["frequency"] not in INTERVALS:
            raise ValueError("请选择有效频率。")
        if values["interval"] not in INTERVALS[values["frequency"]]:
            raise ValueError("采样周期与频率不匹配。")
        if values["category"] not in CATEGORIES or values["stage"] not in STAGES:
            raise ValueError("请选择有效分类和研究阶段。")
        if not values["title"] or not values["definition"]:
            raise ValueError("请填写显示名称和公式 / 研究假设。")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS factors (
                    frequency TEXT NOT NULL, interval TEXT NOT NULL,
                    name TEXT NOT NULL, title TEXT NOT NULL,
                    category TEXT NOT NULL, stage TEXT NOT NULL,
                    definition TEXT NOT NULL, notes TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (frequency, interval, name)
                )
            """)
            if replace:
                cursor = connection.execute(
                    """
                    UPDATE factors SET title=:title, category=:category,
                        stage=:stage, definition=:definition, notes=:notes,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE frequency=:frequency AND interval=:interval AND name=:name
                """,
                    values,
                )
                if cursor.rowcount != 1:
                    raise ValueError("原记录不存在，请刷新后重试。")
            else:
                try:
                    connection.execute(
                        """
                        INSERT INTO factors
                        (frequency, interval, name, title, category, stage, definition, notes)
                        VALUES (:frequency, :interval, :name, :title, :category,
                                :stage, :definition, :notes)
                    """,
                        values,
                    )
                except sqlite3.IntegrityError as exc:
                    raise ValueError(
                        "该频率和采样周期下已有同名因子，请使用编辑入口。"
                    ) from exc
