"""Download six-stock CSMAR validation samples using local password and Duo input."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_lab.data.csmar_sample import DEFAULT_CODES, download_sample, sample_plan
from quant_lab.data.storage_budget import StorageBudget
from quant_lab.data.wrds_access import open_wrds


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", help="WRDS username; never pass a password")
    parser.add_argument("--config", default="configs/data_migration.yaml")
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument(
        "--end", default=str(pd.Timestamp.now(tz="Asia/Shanghai").date() - timedelta(days=1)),
        help="Last requested calendar date; actual available dates appear in the report",
    )
    parser.add_argument("--codes", nargs="+", default=list(DEFAULT_CODES))
    parser.add_argument("--plan", action="store_true", help="Validate locally; no WRDS login")
    args = parser.parse_args()
    config = yaml.safe_load((ROOT / args.config).read_text(encoding="utf-8"))
    storage = config["storage"]
    catalog_path = ROOT / storage["catalog_file"]
    if not catalog_path.is_file():
        raise SystemExit("Account catalog is missing; run probe_wrds_csmar.py first.")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    plan = sample_plan(catalog, tuple(args.codes), args.start, args.end)
    destination = ROOT / storage["csmar_dir"] / "sample"
    budget = StorageBudget(
        ROOT / "data", int(storage["data_budget_gb"] * 1e9),
        int(storage["minimum_free_gb"] * 1e9),
    )
    if not destination.resolve().is_relative_to(budget.root.resolve()):
        raise SystemExit("CSMAR sample directory must remain within project data/.")
    space = budget.check(additional_bytes=10_000_000)
    print(f"Sample: {len(plan['codes'])} stocks, {plan['start']} to {plan['end']}, "
          f"{len(plan['tables'])} tables. No production data switch.")
    print(f"Local data: {space['data_bytes'] / 1e9:.2f} GB; "
          f"disk free: {space['disk_free_bytes'] / 1e9:.2f} GB.")
    print(f"Output: {destination / plan['request_id']}")
    if args.plan:
        print("Plan validated against the account catalog. No data downloaded.")
        return

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    username = (args.username or os.getenv("WRDS_USERNAME") or input("WRDS username: ")).strip()
    if not username:
        raise SystemExit("A WRDS username is required.")
    print("Enter password only at the hidden prompt, then approve Duo on your phone.")
    print("Progress is shown after each table. Re-running the same dates reuses saved tables.")
    try:
        with open_wrds(username) as connection:
            report = download_sample(connection, catalog, plan, destination, budget)
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - redact credentials
        raise SystemExit(
            f"Sample download stopped ({type(exc).__name__}). "
            "Completed table files are retained. Check the last table, local login, "
            "Duo, network and disk space, then rerun with the same dates. "
            "No credentials or raw exception details were saved."
        ) from None
    rows = sum(item["rows"] for item in report["results"].values())
    size = sum(item["bytes"] for item in report["results"].values())
    print(f"Download finished: {rows:,} rows, {size / 1e6:.2f} MB of Parquet samples.")
    if report["empty_tables"]:
        print("Empty tables require review: " + ", ".join(report["empty_tables"]))
    print("Validation is still required. Existing research inputs remain active.")


if __name__ == "__main__":
    main()
