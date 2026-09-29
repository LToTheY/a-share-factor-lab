"""Build the accepted CSMAR-primary research table from local raw staging."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_lab.data.market_dataset import archive_baostock, build_market_dataset
from quant_lab.data.market_staging import build_raw_staging
from quant_lab.data.process_lock import data_lock
from quant_lab.data.storage_budget import StorageBudget


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-staging-through", help="Re-merge local raw observations through YYYY-MM-DD")
    parser.add_argument("--index", default="000905.SH", choices=["000300.SH", "000905.SH"])
    args = parser.parse_args()
    with data_lock(ROOT / "data/state/market_download.lock"):
        archive_baostock(ROOT, StorageBudget(ROOT / "data"))
        if args.refresh_staging_through:
            build_raw_staging(ROOT, "2015-01-01", args.refresh_staging_through, StorageBudget(ROOT / "data"))
        result = build_market_dataset(ROOT, preferred_index=args.index)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
