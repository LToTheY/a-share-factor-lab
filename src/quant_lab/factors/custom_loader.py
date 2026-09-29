"""Reload trusted local Python factors without bytecode caching or config edits."""

from __future__ import annotations

import hashlib
import re
import threading
from pathlib import Path

import numpy as np
import pandas as pd

CUSTOM_DIR = Path(__file__).parent / "custom"
_LOCK = threading.RLock()
_loaded = False
_names: set[str] = set()
_metadata: dict[str, dict] = {}
_results: list[dict] = []


def _checked(function, required):
    def calculate(frame):
        missing = set(required) - set(frame.columns)
        if missing:
            raise ValueError(f"缺少输入字段：{', '.join(sorted(missing))}")
        values = function(frame.copy(deep=True))
        if not isinstance(values, pd.Series):
            raise TypeError("compute 必须返回 pandas.Series")
        if not values.index.equals(frame.index):
            raise ValueError("返回值索引或顺序与输入不一致；请保留 frame.index")
        if not pd.api.types.is_numeric_dtype(values) or pd.api.types.is_bool_dtype(
            values
        ):
            raise ValueError("因子值必须为数值类型，不能是字符串或布尔值")
        values = values.astype(float)
        if np.isinf(values.to_numpy()).any():
            raise ValueError("因子包含无穷值；请处理除零等情况（允许 NaN）")
        return values

    return calculate


def _smoke_check(function, lookback, required):
    from quant_lab.data.synthetic import make_synthetic_daily_data

    panel = make_synthetic_daily_data(5, max(120, lookback + 30))
    for field in ("open", "high", "low", "close"):
        panel[f"adj_{field}"] = panel[field] * panel["adj_factor"]
    panel["turnover_rate"] = panel["volume"] / 1e8
    panel = panel.sort_values(["symbol", "trade_date"])
    missing = set(required) - set(panel.columns)
    if missing:
        raise ValueError(f"模板校验暂不支持字段：{', '.join(sorted(missing))}")
    # Only declared fields are available, alongside the mandatory keys.
    panel = panel[list(dict.fromkeys(["symbol", "trade_date", *required]))]
    values = function(panel)
    if values.notna().sum() == 0:
        raise ValueError("合成样本计算结果全为空，请检查公式和 LOOKBACK")
    cutoff = panel["trade_date"].max() - pd.Timedelta(days=14)
    prefix = panel.loc[panel["trade_date"] <= cutoff]
    pd.testing.assert_series_equal(
        function(prefix),
        values.loc[prefix.index],
        check_names=False,
        rtol=1e-10,
        atol=1e-12,
        obj="截断未来数据后的历史因子值（疑似使用未来数据或全样本统计）",
    )


def refresh_custom_factors(directory: Path | None = None) -> list[dict]:
    """Replace custom entries atomically; invalid/deleted versions are withdrawn."""
    from quant_lab.factors.library import FACTOR_REGISTRY

    global _loaded
    directory = Path(directory) if directory is not None else CUSTOM_DIR
    with _LOCK:
        builtin_names = set(FACTOR_REGISTRY) - _names
        functions, metadata, results = {}, {}, []
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            row = {"file": path.name, "name": path.stem, "status": "失败", "detail": ""}
            try:
                name = path.stem
                if not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", name):
                    raise ValueError(
                        "文件名须以小写字母开头，仅含小写字母、数字、下划线"
                    )
                if name in builtin_names:
                    raise ValueError("与内置因子重名，请更换文件名")
                source = path.read_text(encoding="utf-8-sig")
                namespace = {"__file__": str(path), "__name__": f"user_factor_{name}"}
                # Trusted local plugins: compile fresh source to avoid stale .pyc files.
                exec(compile(source, str(path), "exec"), namespace)  # noqa: S102
                function = namespace.get("compute")
                if not callable(function):
                    raise TypeError("缺少 compute(frame) 函数")
                title = namespace.get("TITLE")
                description = namespace.get("DESCRIPTION")
                if not isinstance(title, str) or not title.strip():
                    raise ValueError("请填写 TITLE 字符串")
                if not isinstance(description, str) or not description.strip():
                    raise ValueError("请填写 DESCRIPTION 字符串")
                lookback = namespace.get("LOOKBACK")
                if type(lookback) is not int or not 0 <= lookback <= 2000:
                    raise ValueError("LOOKBACK 须为 0 到 2000 的整数")
                direction = namespace.get("DIRECTION")
                if type(direction) not in (int, float) or direction not in (-1, 1):
                    raise ValueError("DIRECTION 须为 1 或 -1")
                required = namespace.get("REQUIRED_COLUMNS")
                if (
                    not isinstance(required, (list, tuple))
                    or not required
                    or not all(isinstance(field, str) and field for field in required)
                ):
                    raise ValueError("REQUIRED_COLUMNS 须为非空字段名列表")
                checked = _checked(function, tuple(required))
                _smoke_check(checked, lookback, required)
                functions[name] = checked
                metadata[name] = {
                    "title": title,
                    "description": description,
                    "lookback": lookback,
                    "direction": direction,
                    "required_columns": list(required),
                    "path": str(path),
                    "sha256": hashlib.sha256(source.encode()).hexdigest(),
                    "source": source,
                }
                row.update(
                    status="已加载", detail="接口与合成样本校验通过；尚未验证真实收益"
                )
            except (Exception, SystemExit) as exc:  # noqa: BLE001 -- isolate plugin failures
                row["detail"] = f"{type(exc).__name__}: {str(exc)[:1200]}"
            results.append(row)
        for name in _names:
            FACTOR_REGISTRY.pop(name, None)
        FACTOR_REGISTRY.update(functions)
        _names.clear()
        _names.update(functions)
        _metadata.clear()
        _metadata.update(metadata)
        _results[:] = results
        _loaded = True
        return [dict(row) for row in results]


def ensure_custom_factors_loaded() -> None:
    with _LOCK:
        if not _loaded:
            refresh_custom_factors()


def custom_factor_metadata() -> dict[str, dict]:
    ensure_custom_factors_loaded()
    with _LOCK:
        return {name: dict(meta) for name, meta in _metadata.items()}


def custom_factor_results() -> list[dict]:
    ensure_custom_factors_loaded()
    with _LOCK:
        return [dict(row) for row in _results]
