"""Probe a few historical CSI 500 snapshots before a long backfill."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_lab.data.baostock_client import BaoStockDownloader


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "dates",
        nargs="*",
        default=["2015-01-09", "2020-01-10", "2026-09-09"],
    )
    args = parser.parse_args()
    with BaoStockDownloader() as downloader:
        for date in args.dates:
            snapshot = downloader.zz500_snapshot(date)
            effective = (
                str(snapshot["trade_date"].max().date())
                if not snapshot.empty
                else "EMPTY"
            )
            print(f"requested={date} rows={len(snapshot)} effective={effective}")


if __name__ == "__main__":
    main()
