"""Rebuild the configured ordinary-factor report without downloading data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_lab.research.factor_suite import run_factor_suite
from quant_lab.research.settings import load_research_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/research.yaml")
    args = parser.parse_args()
    settings = load_research_settings(ROOT / args.config)
    market = pd.read_parquet(ROOT / settings.processed_file)
    market.attrs["provider"] = "baostock"
    market.attrs["universe"] = "zz500_local_point_in_time_snapshots"
    market.attrs["research_warning"] = (
        "Dates before the first local CSI 500 snapshot use current-member backfill."
    )
    manifest_path = ROOT / settings.raw_dir / "update_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    summary, _, _ = run_factor_suite(
        market,
        settings,
        ROOT / settings.output_dir,
        next_trading_date=manifest.get("next_trade_date"),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=True))


if __name__ == "__main__":
    main()
