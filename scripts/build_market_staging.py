"""Merge local CSMAR/BaoStock raw observations and report outstanding gaps."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_lab.data.market_staging import build_raw_staging
from quant_lab.data.storage_budget import StorageBudget


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end", required=True, help="Explicit completed trading date: YYYY-MM-DD")
    args = parser.parse_args()
    config = yaml.safe_load((ROOT / "configs/data_migration.yaml").read_text(encoding="utf-8"))
    budget = StorageBudget(ROOT / "data", int(config["storage"]["data_budget_gb"] * 1e9),
                           int(config["storage"]["minimum_free_gb"] * 1e9))
    budget.check(250_000_000)
    report = build_raw_staging(ROOT, config["history_start"], args.end, budget)
    print(json.dumps({k: v for k, v in report.items() if k != "partitions"}, indent=2))


if __name__ == "__main__":
    main()
