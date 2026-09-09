"""Download free real daily data for historical CSI 500 constituents."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_lab.data.baostock_client import BaoStockDownloader


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--output", default="data/raw/baostock")
    args = parser.parse_args()
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    with BaoStockDownloader() as downloader:
        manifest = downloader.download_zz500(args.start, args.end, output)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"Raw data: {output}")
    if manifest["failures"]:
        raise SystemExit("Some symbols failed; rerun to resume and inspect manifest.")


if __name__ == "__main__":
    main()
