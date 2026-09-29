"""Download the full historical HS300/CSI500 CSMAR scope with one local login."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_lab.data.csmar_bulk import (
    BULK_TABLES,
    build_partitions,
    download_bulk,
    validate_catalog,
    validate_codes,
)
from quant_lab.data.storage_budget import StorageBudget
from quant_lab.data.wrds_access import open_wrds


def load_completed_scope(config: dict, end: str) -> tuple[str, ...] | None:
    """Incomplete snapshot caches are never treated as the entire historical pool."""
    reference = ROOT / config["storage"]["reference_dir"]
    manifest_path = reference / "manifest.json"
    membership_path = reference / "index_membership.parquet"
    symbols_path = reference / "historical_symbols.parquet"
    if not all(path.is_file() for path in [manifest_path, membership_path, symbols_path]):
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("status") != "reference_only_not_activated"
        or manifest.get("history_start") != config["history_start"]
        or manifest.get("requested_end") != end
        or sorted(manifest.get("indices", [])) != sorted(config["indices"])
        or manifest.get("membership_frequency", "W-FRI") != config["membership_frequency"]
    ):
        return None
    members = pd.read_parquet(membership_path)
    symbols = pd.read_parquet(symbols_path)["symbol"]
    if (
        len(members) != manifest.get("membership_rows")
        or len(symbols) != manifest.get("historical_symbols")
        or symbols.duplicated().any()
        or set(symbols) != set(members["symbol"])
        or set(members["index_code"]) != set(config["indices"])
        or not symbols.str.fullmatch(r"[0-9]{6}\.(SH|SZ)").all()
    ):
        raise ValueError("Completed reference manifest does not match historical pool files")
    member_dates = pd.to_datetime(members["trade_date"])
    if member_dates.isna().any() or (member_dates > pd.Timestamp(end)).any():
        raise ValueError("Reference history contains missing/future effective dates")
    codes = tuple(sorted(symbols.str.slice(0, 6)))
    validate_codes(codes)
    if manifest.get("incomplete_snapshot_requests", 0):
        print(f"Membership note: {manifest['incomplete_snapshot_requests']} requests have "
              "one missing provider member; kept flagged for later review.", flush=True)
    return codes


@contextmanager
def download_lock(path: Path):
    """OS-managed lock prevents concurrent writers and releases after a crash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise RuntimeError("Another CSMAR bulk download is running") from None
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/data_migration.yaml")
    parser.add_argument("--username", help="Optional WRDS username; never pass a password")
    parser.add_argument(
        "--end", default=str(pd.Timestamp.now(tz="Asia/Shanghai").date() - timedelta(days=1)),
        help="Requested calendar end date (default: yesterday in Shanghai)",
    )
    parser.add_argument("--plan", action="store_true", help="Local checks only; no login or writes")
    parser.add_argument("--refresh", action="store_true", help="Refresh even verified cached partitions")
    args = parser.parse_args()
    config_path = ROOT / args.config
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    end = date.fromisoformat(args.end).isoformat()
    if date.fromisoformat(end) > pd.Timestamp.now(tz="Asia/Shanghai").date():
        raise SystemExit("Requested end must not be in the future.")
    storage = config["storage"]
    catalog = json.loads((ROOT / storage["catalog_file"]).read_text(encoding="utf-8"))
    validate_catalog(catalog)
    budget = StorageBudget(
        ROOT / "data", int(storage["data_budget_gb"] * 1e9),
        int(storage["minimum_free_gb"] * 1e9),
    )
    destination = ROOT / storage["csmar_dir"] / "bulk"
    reference = ROOT / storage["reference_dir"]
    for path in [destination, reference]:
        if not path.resolve().is_relative_to(budget.root.resolve()):
            raise SystemExit("Output directories must remain within project data/.")
    space = budget.check(additional_bytes=16_000_000)
    indices = tuple(code.split(".")[0] for code in config["indices"])
    codes = load_completed_scope(config, end)
    part_count = len(build_partitions(codes or ("000001",), indices,
                                     config["history_start"], end))
    print(f"CSMAR bulk: {config['history_start']} to {end}, {len(BULK_TABLES)} tables, "
          f"{part_count} bounded partitions.", flush=True)
    print(f"Historical HS300 + CSI500 union: {len(codes) if codes else 'being prepared'} stocks.")
    print(f"Disk free: {space['disk_free_bytes'] / 1e9:.2f} GB; "
          f"data budget: {storage['data_budget_gb']} GB; reserve: {storage['minimum_free_gb']} GB.")
    print("This run downloads CSMAR market/financial data; BaoStock prices will be filled later.")
    print(f"Output: {destination}", flush=True)
    if args.plan:
        print("Account fields validated. Plan only: no login, writes or downloads.")
        return
    with download_lock(ROOT / "data/state/csmar_bulk.lock"):
        if codes is None:
            print("Preparing historical constituent lists only (no BaoStock prices). "
                  "WRDS login follows this step.", flush=True)
            subprocess.run([
                sys.executable, "-u", str(ROOT / "scripts/prepare_shared_universe.py"),
                "--config", str(config_path), "--end", end,
            ], cwd=ROOT, check=True)
            codes = load_completed_scope(config, end)
            if codes is None:
                raise SystemExit("Historical pool is incomplete. Re-run to resume; no WRDS download started.")
        print(f"Historical pool ready: {len(codes)} stocks. Starting full CSMAR download.", flush=True)
        scope_metadata = json.loads((reference / "manifest.json").read_text(encoding="utf-8"))
        scope_metadata["membership_sha256"] = hashlib.sha256(
            (reference / "index_membership.parquet").read_bytes()
        ).hexdigest()
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
        username = (args.username or os.getenv("WRDS_USERNAME") or input("WRDS username: ")).strip()
        if not username:
            raise SystemExit("A WRDS username is required.")
        print("Enter password at the hidden prompt, approve Duo, then leave this window open.", flush=True)
        try:
            with open_wrds(username) as connection:
                report = download_bulk(
                    connection, catalog, codes, indices, config["history_start"], end,
                    destination, budget, refresh=args.refresh,
                    universe_reference=scope_metadata,
                    progress=lambda message: print(message, flush=True),
                )
        except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - redact credentials
            raise SystemExit(
                f"Bulk download stopped ({type(exc).__name__}). "
                "Completed partitions are retained; rerun the same command to resume. "
                f"See progress in {destination / 'manifest.json'}. "
                "No raw error or credentials were saved."
            ) from None
        print(f"CSMAR bulk download finished: {report['rows']:,} rows; "
              f"{report['parquet_bytes'] / 1e9:.3f} GB Parquet.")
        for table, coverage in report["coverage"].items():
            print(f"  {table}: {coverage['rows']:,} rows, latest {coverage['max_date']}, "
                  f"{coverage['observed_codes']} codes")
        print("Next: inspect coverage, fill BaoStock gaps, normalize and validate, "
              "then replace the active dataset and remove replaced legacy prices.")


if __name__ == "__main__":
    main()
