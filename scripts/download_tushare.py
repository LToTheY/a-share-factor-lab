"""Download resumable Tushare daily partitions after TUSHARE_TOKEN is set."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_lab.data.tushare_client import TushareDownloader


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="YYYYMMDD")
    parser.add_argument("--end", required=True, help="YYYYMMDD")
    parser.add_argument("--output", default="data/raw/tushare/daily")
    parser.add_argument("--index-code", default="000905.SH")
    args = parser.parse_args()
    _load_dotenv_if_available()
    if not os.getenv("TUSHARE_TOKEN"):
        raise SystemExit(
            "Missing TUSHARE_TOKEN. Copy .env.example to .env and fill it."
        )
    output = ROOT / args.output
    paths = TushareDownloader().download_range(args.start, args.end, output)
    print(f"Cached {len(paths)} trading dates under {output}")
    index_path = (
        ROOT
        / "data"
        / "raw"
        / "tushare"
        / f"index_{args.index_code.replace('.', '_')}.parquet"
    )
    TushareDownloader().download_index_weights(
        args.index_code, args.start, args.end, index_path
    )
    print(f"Cached index snapshots: {index_path}")


if __name__ == "__main__":
    main()
