"""Prepare shared HS300/CSI500 history; do not download or switch market prices."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_lab.data.baostock_client import BaoStockDownloader
from quant_lab.data.daily_update import latest_completed_session
from quant_lab.data.index_membership import collect_memberships
from quant_lab.data.storage import write_table
from quant_lab.data.storage_budget import StorageBudget


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/data_migration.yaml")
    parser.add_argument("--end", help="Last requested date (default: last completed session)")
    args = parser.parse_args()
    config = yaml.safe_load((ROOT / args.config).read_text(encoding="utf-8"))
    storage = config["storage"]
    budget = StorageBudget(
        ROOT / "data", int(storage["data_budget_gb"] * 1e9),
        int(storage["minimum_free_gb"] * 1e9),
    )
    budget.check(additional_bytes=20_000_000)
    destination = ROOT / storage["reference_dir"]
    now = pd.Timestamp.now(tz="Asia/Shanghai").tz_localize(None)
    requested_end = pd.Timestamp(args.end) if args.end else now.normalize()
    with BaoStockDownloader() as downloader:
        calendar = downloader.trade_calendar(
            config["history_start"], requested_end.strftime("%Y-%m-%d")
        )
        end = requested_end if args.end else latest_completed_session(calendar, now)
        calendar = calendar.loc[calendar["trade_date"] <= end].copy()
        memberships, counts = collect_memberships(
            downloader, calendar, destination, ROOT / "data/raw/baostock_daily",
            config["indices"], config["membership_frequency"], budget,
            allow_one_missing_for_download=config.get(
                "allow_one_missing_member_for_download_scope", False
            ),
        )
    budget.check(additional_bytes=20_000_000)
    write_table(calendar, destination / "trade_calendar.parquet")
    write_table(memberships, destination / "index_membership.parquet")
    symbols = memberships[["symbol"]].drop_duplicates().sort_values("symbol")
    write_table(symbols, destination / "historical_symbols.parquet")
    manifest = {
        "provider": "baostock",
        "status": "reference_only_not_activated",
        "history_start": config["history_start"],
        "requested_end": str(end.date()),
        "indices": config["indices"],
        "membership_frequency": config["membership_frequency"],
        "request_history_complete": True,
        "all_snapshots_full": counts["incomplete_snapshot_requests"] == 0,
        "incomplete_effective_dates": memberships.loc[
            ~memberships["snapshot_complete"], ["index_code", "trade_date"]
        ].drop_duplicates().assign(
            trade_date=lambda frame: frame["trade_date"].dt.strftime("%Y-%m-%d")
        ).to_dict("records"),
        "historical_symbols": len(symbols),
        "membership_rows": len(memberships),
        **counts,
        **budget.check(),
        "limitation": "Periodic snapshots, not exact daily change records. "
        "No future snapshot is used before its provider date. "
        "CSMAR membership should replace this fallback if available and verified.",
    }
    path = destination / "manifest.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    temporary.replace(path)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
