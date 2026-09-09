"""Backfill one disjoint shard of the cached BaoStock historical universe."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_lab.data.baostock_client import BaoStockDownloader
from quant_lab.data.daily_update import _append_partition
from quant_lab.research.settings import load_research_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/research.yaml")
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--shards", type=int, required=True)
    parser.add_argument("--end", default=None)
    args = parser.parse_args()
    if args.shards < 1 or not 0 <= args.shard < args.shards:
        raise SystemExit("Require 0 <= --shard < --shards")

    settings = load_research_settings(ROOT / args.config)
    base = ROOT / settings.raw_dir
    snapshots = sorted((base / "membership_snapshots").glob("*.parquet"))
    historical_files = [path for path in snapshots if "_request_" in path.name]
    current_files = [path for path in snapshots if "_request_" not in path.name]
    if not historical_files or not current_files:
        raise SystemExit("Run daily_update.py once to cache membership snapshots first")

    historical = pd.concat(
        [pd.read_parquet(path, columns=["symbol"]) for path in historical_files],
        ignore_index=True,
    )
    current = pd.read_parquet(current_files[-1], columns=["symbol"])
    current_symbols = set(current["symbol"].unique())
    all_symbols = sorted(historical["symbol"].unique())
    symbols = all_symbols[args.shard :: args.shards]
    start = pd.Timestamp(settings.history_start_date)
    end = pd.Timestamp(args.end or pd.Timestamp.now().date())
    failures: list[dict[str, str]] = []

    with BaoStockDownloader() as downloader:
        for number, symbol in enumerate(symbols, start=1):
            for dataset, directory, adjustflag in [
                ("adjusted", base / "daily", "1"),
                ("raw", base / "raw_daily", "3"),
            ]:
                path = directory / f"{symbol.replace('.', '_')}.parquet"
                try:
                    _append_partition(
                        downloader,
                        symbol,
                        path,
                        start,
                        end,
                        adjustflag,
                        include_suffix=symbol in current_symbols,
                    )
                except Exception as exc:  # noqa: BLE001 - provider failures are logged
                    failures.append(
                        {"symbol": symbol, "dataset": dataset, "error": str(exc)}
                    )
            if number % 10 == 0 or number == len(symbols):
                print(
                    f"shard={args.shard} progress={number}/{len(symbols)} "
                    f"failures={len(failures)}",
                    flush=True,
                )
    failure_path = base / f"backfill_failures_shard_{args.shard}.json"
    failure_path.write_text(
        json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if failures:
        raise SystemExit(
            f"Shard {args.shard} completed with {len(failures)} failed datasets; "
            "rerun the same shard to resume"
        )

    failure_path = base / f"backfill_failures_shard_{args.shard}.json"
    failure_path.write_text(
        json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if failures:
        raise SystemExit(f"Shard {args.shard} completed with {len(failures)} failures")


if __name__ == "__main__":
    main()
