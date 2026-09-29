"""Versioned CSMAR-priority price dataset and independent BaoStock archive."""

from __future__ import annotations

import gc
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from quant_lab.data.csmar_sample import _write_json
from quant_lab.data.index_membership import INDEX_LABELS, attach_shared_memberships
from quant_lab.data.market_staging import KEYS, MARKET, _register_baostock
from quant_lab.data.research_prices import prepare_price_panel
from quant_lab.data.storage_budget import StorageBudget, write_budgeted_frame

DATASET_RELATIVE = "data/processed/market_daily.parquet"
MANIFEST_RELATIVE = "data/processed/market_daily.manifest.json"
POLICY_VERSION = "csmar-priority-preclose-chain-observed-members-v1"
RESEARCH_WARNING = (
    "CSMAR优先、BaoStock补缺；交易所昨收链复权，不等同现金分红再投资账本。"
    "历史成员按当时已知快照沿用，5次请求对应4个生效日仅有499名成员；"
    "只使用已知成员，不猜补缺失股票，也不声称精确复现官方指数。"
    "2条历史成交额异常保留原值、屏蔽对应因子输入及模拟成交。"
    "未验证的财报、市值及静态行业不进入当前价量模型。"
)

CANONICAL_COLUMNS = [
    *KEYS, *MARKET, "adj_factor", "adj_open", "adj_high", "adj_low", "adj_close", "adj_preclose",
    "turnover_rate", "is_st", "is_suspended", "is_st_known", "is_suspended_known",
    "is_limit_up", "is_limit_down", "up_limit", "down_limit", "limit_status_known",
    "is_no_limit_session", "listing_age_sessions", "list_date", "research_segment",
    "was_suspension_filled", "amount_outside_price_range", "is_usable_market_data",
    "in_hs300", "in_zz500", "in_csi800", "in_index", "membership_complete",
    "hs300_membership_complete", "zz500_membership_complete", "membership_date",
    *[f"{column}_source" for column in MARKET], "status_source",
]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_baostock(root: Path, budget: StorageBudget) -> dict:
    """Preserve all normalized BaoStock values before removing legacy caches."""
    destination = root / "data/raw/baostock_consolidated"
    if (destination / "manifest.json").exists():
        report = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
        for part in report["partitions"]:
            if file_sha256(destination / part["file"]) != part["sha256"]:
                raise ValueError("BaoStock archive checksum mismatch")
        return report
    pending = root / "data/raw/baostock_consolidated.building"
    pending.mkdir(parents=True, exist_ok=True)
    report = {"provider": "baostock", "status": "building", "partitions": [], "rows": 0}
    with duckdb.connect() as connection:
        connection.execute("SET memory_limit='1GB'")
        connection.execute("SET threads=2")
        _register_baostock(connection, root)
        views = [row[0] for row in connection.execute("SHOW TABLES").fetchall() if row[0].startswith("bao_") or row[0] == "crosses"]
        bounds = [connection.execute(f'SELECT min(trade_date),max(trade_date) FROM "{view}"').fetchone() for view in views]
        first = min(pd.Timestamp(row[0]).year for row in bounds if row[0] is not None)
        last = max(pd.Timestamp(row[1]).year for row in bounds if row[1] is not None)
        for year in range(first, last + 1):
            frame = connection.execute("SELECT * FROM bao WHERE trade_date BETWEEN ? AND ? ORDER BY symbol,trade_date", [f"{year}-01-01", f"{year}-12-31"]).df()
            path = pending / f"{year}.parquet"
            write_budgeted_frame(frame, path, budget)
            if not frame.equals(pd.read_parquet(path)):
                raise ValueError("BaoStock archive changed source observations")
            report["partitions"].append({"file": path.name, "rows": len(frame), "sha256": file_sha256(path), "bytes": path.stat().st_size})
            report["rows"] += len(frame)
            print(f"BaoStock independent archive {year}: {len(frame):,} rows", flush=True)
    report.update(status="verified", source_values_verified=True,
                  completed_at_utc=datetime.now(timezone.utc).isoformat())
    _write_json(pending / "manifest.json", report, budget)
    pending.rename(destination)
    return report


def build_market_dataset(root: Path, *, preferred_index="000905.SH", chunk_size=64) -> dict:
    """Build one compact, research-ready price table without loading all raw fields."""
    budget = StorageBudget(root / "data")
    stage = root / "data/processed/csmar_staging"
    source = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))
    if source["missing_baostock_sessions"] or source["missing_merged_sessions"]:
        raise ValueError("Raw daily coverage must pass before activating the dataset")
    if source.get("invalid_market_rows", 0):
        raise ValueError("Invalid raw OHLC/activity must be resolved before activation")
    for part in source["partitions"]:
        if file_sha256(stage / "raw_daily" / f"{part['year']}.parquet") != part["sha256"]:
            raise ValueError("Raw staging partition checksum mismatch")
    if preferred_index not in INDEX_LABELS:
        raise ValueError("Unsupported preferred index")
    reference = root / "data/raw/market_reference"
    calendar = pd.read_parquet(reference / "trade_calendar.parquet")
    sessions = pd.DatetimeIndex(calendar.loc[calendar["is_trading_day"], "trade_date"])
    basics = pd.read_parquet(root / "data/raw/baostock_supplement/reference/stock_basic.parquet")
    memberships = pd.read_parquet(reference / "index_membership.parquet")
    symbols = sorted(pd.read_parquet(reference / "historical_symbols.parquet")["symbol"])
    dataset_id = hashlib.sha256((POLICY_VERSION + preferred_index + file_sha256(stage / "manifest.json")
                                + file_sha256(reference / "index_membership.parquet")
                                + file_sha256(root / "data/raw/baostock_supplement/reference/stock_basic.parquet")).encode()).hexdigest()
    output = root / DATASET_RELATIVE
    temporary = output.with_suffix(".building.parquet")
    budget.check(500_000_000)
    report = {"status": "building", "dataset_id": dataset_id, "policy_version": POLICY_VERSION,
              "provider": "csmar_baostock", "preferred_index": preferred_index, "rows": 0,
              "symbols": len(symbols), "end_date": source["end"], "start_date": source["start"],
              "research_warning": RESEARCH_WARNING, "amount_anomaly_rows": 0,
              "unknown_limit_rows": 0, "allow_observed_membership_subset": True,
              "financial_factors_ready": False, "price_research_ready": False}
    writer = None
    selected = [*KEYS, *MARKET, "turnover_rate", "is_st", "is_suspended", "is_st_known",
                "is_suspended_known", "up_limit", "down_limit", *[f"{c}_source" for c in MARKET], "status_source"]
    with duckdb.connect() as connection:
        connection.execute("SET memory_limit='1GB'")
        connection.execute("SET threads=2")
        paths = [str(stage / "raw_daily" / f"{p['year']}.parquet") for p in source["partitions"]]
        connection.read_parquet(paths, union_by_name=True).create_view("raw")
        try:
            for offset in range(0, len(symbols), chunk_size):
                connection.register("wanted", pd.DataFrame({"symbol": symbols[offset:offset + chunk_size]}))
                columns = ",".join(f'r."{c}"' for c in selected)
                raw = connection.execute(f"SELECT {columns} FROM raw r JOIN wanted USING(symbol) ORDER BY symbol,trade_date").df()
                panel = prepare_price_panel(raw, sessions, basics)
                panel = attach_shared_memberships(panel, memberships, allow_observed_subset=True)
                label = INDEX_LABELS[preferred_index]
                panel["in_index"] = panel[f"in_{label}"]
                panel["membership_complete"] = panel[f"{label}_membership_complete"]
                panel["membership_date"] = panel[f"{label}_membership_date"]
                prices = panel[["open", "high", "low", "close", "adj_open", "adj_close"]]
                if prices.isna().any(axis=None) or not np.isfinite(prices).all(axis=None) or prices.le(0).any(axis=None):
                    raise ValueError("Unresolved raw/adjusted prices in canonical dataset")
                if not panel[["is_st_known", "is_suspended_known"]].all(axis=None):
                    raise ValueError("Unknown historical ST/suspension status")
                if panel.loc[~panel["is_suspended"], ["volume", "amount"]].isna().any(axis=None):
                    raise ValueError("Unresolved active-market activity")
                panel["is_usable_market_data"] = ~panel["amount_outside_price_range"]
                report["rows"] += len(panel)
                report["amount_anomaly_rows"] += int(panel["amount_outside_price_range"].sum())
                report["unknown_limit_rows"] += int((~panel["limit_status_known"]).sum())
                table = pa.Table.from_pandas(panel[CANONICAL_COLUMNS].sort_values(KEYS), preserve_index=False)
                if writer is None:
                    writer = pq.ParquetWriter(temporary, table.schema, compression="zstd")
                writer.write_table(table, row_group_size=100_000)
                print(f"Canonical prices: {min(offset + chunk_size, len(symbols))}/{len(symbols)} stocks", flush=True)
                del raw, panel, table
                gc.collect()
        finally:
            if writer is not None:
                writer.close()
        connection.read_parquet(str(temporary)).create_view("canonical")
        count, unique = connection.execute("SELECT count(*), count(DISTINCT (trade_date,symbol)) FROM canonical").fetchone()
        if count != source["rows"] or count != unique:
            raise ValueError("Canonical row coverage or unique keys differ from raw staging")
        report["latest_symbols"] = connection.execute("SELECT count(*) FROM canonical WHERE trade_date=?", [source["end"]]).fetchone()[0]
        report["latest_index_symbols"] = connection.execute("SELECT count(*) FROM canonical WHERE trade_date=? AND in_index", [source["end"]]).fetchone()[0]
        report["incomplete_membership_sessions"] = connection.execute("SELECT count(DISTINCT trade_date) FROM canonical WHERE NOT membership_complete").fetchone()[0]
        report["unknown_limit_member_rows"] = connection.execute("SELECT count(*) FROM canonical WHERE in_index AND NOT limit_status_known").fetchone()[0]
    if report["latest_index_symbols"] != (300 if preferred_index == "000300.SH" else 500):
        raise ValueError("Current canonical member coverage is incomplete")
    budget.check()
    temporary.replace(output)
    future = sessions[sessions > pd.Timestamp(source["end"])]
    report.update(status="ready", price_research_ready=True, path=DATASET_RELATIVE,
                  sha256=file_sha256(output), bytes=output.stat().st_size,
                  next_trade_date=str(future.min().date()) if len(future) else None,
                  completed_at_utc=datetime.now(timezone.utc).isoformat())
    _write_json(root / MANIFEST_RELATIVE, report, budget)
    return report


def load_market_manifest(path: Path) -> dict:
    manifest = path.with_suffix(".manifest.json")
    return json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}


def load_research_market(path: Path, *, columns=None) -> pd.DataFrame:
    manifest = load_market_manifest(path)
    if manifest and not manifest.get("price_research_ready"):
        raise ValueError("Canonical market dataset has not passed price acceptance")
    if columns is None and manifest:
        columns = ["trade_date", "symbol", "open", "close", "adj_close", "volume", "amount",
                   "turnover_rate", "is_st", "is_suspended", "is_st_known", "is_suspended_known",
                   "is_limit_up", "is_limit_down", "limit_status_known", "in_index",
                   "is_usable_market_data", "amount_outside_price_range", "research_segment", "listing_age_sessions"]
    frame = pd.read_parquet(path, columns=columns)
    frame.attrs.update(provider=manifest.get("provider", "external"),
                       dataset_id=manifest.get("dataset_id"),
                       research_warning=manifest.get("research_warning", "External data; verify its conventions."))
    return frame


def require_matching_dataset(score_file: Path, market_file: Path) -> None:
    market = load_market_manifest(market_file)
    if not market:
        return
    provenance = score_file.parent / "dataset_provenance.json"
    if not provenance.exists() or json.loads(provenance.read_text(encoding="utf-8")).get("dataset_id") != market["dataset_id"]:
        raise ValueError("行情与因子分数的数据版本不一致，请先重建因子研究报告")
