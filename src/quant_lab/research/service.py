"""Versioned research requests shared by the local UI and command line."""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow.parquet as pq
import yaml

from quant_lab.data.market_dataset import load_market_manifest
from quant_lab.factors.custom_loader import (
    custom_factor_metadata,
    refresh_custom_factors,
)
from quant_lab.research.factor_suite import run_factor_suite
from quant_lab.research.settings import load_research_settings

POOLS = {"000300.SH": "in_hs300", "000905.SH": "in_zz500", "CSI800": "in_csi800"}
BUILTIN_INPUTS = {
    "momentum_20_5": (20, ["adj_close"]),
    "momentum_60_5": (60, ["adj_close"]),
    "momentum_120_20": (120, ["adj_close"]),
    "reversal_5": (5, ["adj_close"]),
    "reversal_20": (20, ["adj_close"]),
    "volatility_20": (20, ["adj_close"]),
    "volatility_60": (60, ["adj_close"]),
    "amihud_20": (20, ["adj_close", "amount"]),
    "turnover_mean_20": (20, ["turnover_rate"]),
    "amount_momentum_20": (20, ["amount"]),
    "price_volume_corr_20": (20, ["adj_close", "amount"]),
    "ivol_60": (120, ["adj_close"]),
    "downside_volatility_20": (20, ["adj_close"]),
    "overnight_reversal_5": (5, ["adj_open", "adj_close"]),
    "intraday_momentum_20": (20, ["open", "close"]),
}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def file_digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def code_version(root: Path) -> str:
    return digest({str(p.relative_to(root)): file_digest(p)
                   for p in sorted((root / "src/quant_lab").rglob("*.py"))})


def factor_version(root: Path) -> str:
    paths = [*(root / "src/quant_lab/factors").rglob("*.py"),
             root / "src/quant_lab/evaluation/preprocess.py", root / "src/quant_lab/universe/filters.py",
             root / "src/quant_lab/evaluation/diagnostics.py",
             root / "src/quant_lab/research/factor_suite.py", root / "src/quant_lab/research/service.py",
             root / "src/quant_lab/research/settings.py"]
    return digest({str(p.relative_to(root)): file_digest(p) for p in sorted(paths)})


def runtime_manifest() -> dict:
    """Record numerical dependencies without usernames, machine names or paths."""
    packages = {}
    for name in ("numpy", "pandas", "duckdb", "pyarrow", "scipy", "scikit-learn"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return {"python": platform.python_version(), "system": platform.system(), "packages": packages}


def local_path(root: Path, value: str, *, area: str | None = None) -> Path:
    path = (root / value).resolve()
    if not path.is_relative_to((root / area).resolve() if area else root.resolve()):
        raise ValueError("路径必须位于项目指定目录内")
    return path


def dataset_version(path: Path) -> dict:
    if not path.is_file():
        raise ValueError("缺少行情数据；请更新数据或先运行合成演示")
    manifest = load_market_manifest(path)
    sha = file_digest(path)
    if manifest and (not manifest.get("price_research_ready") or manifest.get("sha256") != sha):
        raise ValueError("行情文件未验收或与数据清单校验和不一致，请重新检查数据")
    return {"dataset_id": manifest.get("dataset_id", sha), "sha256": sha,
            "provider": manifest.get("provider", "external"),
            "end_date": manifest.get("end_date"), "warning": manifest.get("research_warning", "")}


@dataclass(frozen=True)
class ResearchRequest:
    config: dict
    dataset: dict
    code_version: str
    factor_version: str
    runtime: dict = field(default_factory=runtime_manifest)

    @classmethod
    def create(cls, root: Path, config: dict):
        refresh_custom_factors()
        path = local_path(root, config["data"]["processed_file"])
        return cls(config, dataset_version(path), code_version(root), factor_version(root))


def load_inputs(path: Path, settings) -> pd.DataFrame:
    """Read only needed columns and enough preceding sessions, never future rows."""
    available = set(pq.read_schema(path).names)
    custom = custom_factor_metadata()
    required = {"trade_date", "symbol", "open", "close", "adj_close", "amount", "is_st",
                "is_suspended", "is_st_known", "is_suspended_known", "is_limit_up",
                "is_limit_down", "limit_status_known"}
    lookback = 0
    for definition in settings.factors:
        if definition.name in custom:
            item = custom[definition.name]
            window, fields = item["lookback"], item["required_columns"]
        elif definition.name in BUILTIN_INPUTS:
            window, fields = BUILTIN_INPUTS[definition.name]
        else:
            raise ValueError(f"因子 {definition.name} 尚未声明可用的价量输入")
        lookback = max(lookback, window)
        required.update(fields)
    pool = POOLS.get(settings.preferred_index)
    if pool is None:
        raise ValueError("请选择历史沪深300、中证500或两者并集")
    required.add(pool)
    if settings.neutralize_size:
        required.add("market_cap")
    if settings.neutralize_industry:
        required.add("industry")
    missing = sorted(required - available)
    if missing:
        raise ValueError("行情缺少因子/股票池所需字段：" + ", ".join(missing))
    columns = sorted(required | (available & {"adj_open", "preclose", "up_limit", "down_limit", "listing_age_sessions", "is_usable_market_data",
                                               "research_segment", "amount_outside_price_range"}))
    with duckdb.connect() as con:
        con.execute("SET threads=2")
        con.execute("SET memory_limit='1GB'")
        dates = pd.DatetimeIndex(con.execute(
            "SELECT DISTINCT trade_date FROM read_parquet(?) ORDER BY trade_date", [str(path)]
        ).df().trade_date)
        start = pd.Timestamp(settings.research_start_date or dates[min(lookback, len(dates)-1)])
        end = pd.Timestamp(settings.research_end_date or dates[-1])
        warmup = max(lookback, settings.min_listed_days if "listing_age_sessions" not in available else 0)
        position = dates.searchsorted(start)
        if position < warmup or position >= len(dates) or start >= end or end > dates[-1]:
            raise ValueError(f"研究区间或预热历史不足：需要开始日前至少 {warmup} 个交易日，行情截至 {dates[-1].date()}")
        columns_sql = ",".join('"' + name.replace('"', '""') + '"' for name in columns)
        frame = con.execute(f"SELECT {columns_sql} FROM read_parquet(?) WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date,symbol",
                            [str(path), dates[position - warmup], end]).df()
    frame["trade_date"] = pd.to_datetime(frame.trade_date)
    frame["in_index"] = frame[pool].fillna(False)
    manifest = load_market_manifest(path)
    frame.attrs.update(provider=manifest.get("provider", "external"), dataset_id=manifest.get("dataset_id"),
                       research_warning=manifest.get("research_warning", "外部数据，请核对口径"))
    return frame


def run_research(root: Path, request: ResearchRequest, output: Path, progress=lambda message: None) -> dict:
    """Write into a private candidate directory; caller publishes only on success."""
    path = local_path(root, request.config["data"]["processed_file"])
    if (dataset_version(path) != request.dataset or code_version(root) != request.code_version
            or request.runtime != runtime_manifest()):
        raise ValueError("排队后数据或代码已变化，请重新提交研究")
    output.mkdir(parents=True, exist_ok=True)
    config_path = output / "effective_config.yaml"
    config_path.write_text(yaml.safe_dump(request.config, allow_unicode=True), encoding="utf-8")
    settings = load_research_settings(config_path)
    progress("检查字段与预热窗口")
    market = load_inputs(path, settings)
    market.attrs["dataset_id"] = request.dataset["dataset_id"]
    summary, _, _ = run_factor_suite(market, settings, output, None, progress=progress)
    progress("核验版本并发布结果")
    if (dataset_version(path) != request.dataset or code_version(root) != request.code_version
            or request.runtime != runtime_manifest()):
        raise ValueError("运行期间数据或代码发生变化；候选结果未发布，请重新运行")
    custom = {name: item for name, item in custom_factor_metadata().items()
              if name in {f.name for f in settings.factors}}
    provenance = {**request.dataset, "code_version": request.code_version, "runtime": request.runtime,
                  "factor_version": request.factor_version, "universe": settings.preferred_index,
                  "market_file": str(path.relative_to(root)), "custom_factors": custom,
                  "request_fingerprint": digest(request.config),
                  "score_sha256": file_digest(output / "factor_scores.parquet")}
    (output / "dataset_provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "result_manifest.json").write_text(json.dumps({"status": "succeeded", "schema_version": 1,
        "data_through": summary["end_date"], "provenance": provenance, "config": request.config},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def create_demo(root: Path) -> dict:
    """Deterministic, explicitly synthetic 10-year input with no licensed data."""
    from quant_lab.data.synthetic import make_synthetic_daily_data

    path = root / "data/processed/demo_market.parquet"
    if not path.exists():
        frame = make_synthetic_daily_data(30, 2600, "2015-01-01", seed=42)
        for field in ("open", "high", "low", "close"):
            frame[f"adj_{field}"] = frame[field]
        frame["turnover_rate"] = frame.volume / 1e8
        frame["listing_age_sessions"] = 500 + frame.groupby("symbol").cumcount()
        rank = frame.symbol.str[:6].astype(int)
        frame["in_hs300"] = rank <= 15
        frame["in_zz500"] = rank > 15
        frame["in_csi800"] = True
        frame["in_index"] = frame.in_zz500
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path, index=False)
        sha = file_digest(path)
        path.with_suffix(".manifest.json").write_text(json.dumps({"provider": "synthetic",
            "price_research_ready": True, "dataset_id": sha, "sha256": sha,
            "research_warning": "合成数据演示：不能用来声称真实策略收益"}), encoding="utf-8")
    config = yaml.safe_load((root / "configs/research.yaml").read_text(encoding="utf-8"))
    config["data"].update(processed_file=str(path.relative_to(root)), source="synthetic",
                           research_start_date="2016-01-01", research_end_date="2024-12-18")
    config["universe"].update(preferred_index="CSI800", min_amount=0)
    config["factors"]["definitions"] = [{"name": "reversal_5", "direction": 1.0}]
    config["factors"]["minimum_valid_factors"] = 1
    return config
