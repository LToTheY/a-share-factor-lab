"""Reuse existing raw prices and fill remaining BaoStock observations."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_lab.data.baostock_client import BaoStockDownloader
from quant_lab.data.baostock_supplement import fill_gaps
from quant_lab.data.csmar_sample import _write_json
from quant_lab.data.process_lock import data_lock
from quant_lab.data.storage_budget import StorageBudget


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end", required=True, help="Explicit completed trading date: YYYY-MM-DD")
    parser.add_argument("--config", default="configs/data_migration.yaml")
    args = parser.parse_args()
    config = yaml.safe_load((ROOT / args.config).read_text(encoding="utf-8"))
    budget = StorageBudget(ROOT / "data", int(config["storage"]["data_budget_gb"] * 1e9),
                           int(config["storage"]["minimum_free_gb"] * 1e9))
    budget.check(20_000_000)
    client = BaoStockDownloader()
    connection_status = ROOT / "data/raw/baostock_supplement/connection_status.json"
    for attempt in range(1, 4):
        try:
            client.reconnect()
            break
        except Exception as exc:  # noqa: BLE001 - vendor network failures vary by response
            _write_json(connection_status, {
                "status": "connection_failed", "attempt": attempt, "error_type": type(exc).__name__,
                "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                "cached_data_preserved": True,
            }, budget)
            if attempt == 3:
                raise SystemExit("BaoStock unavailable after three login attempts") from None
            print(f"Login failed; retry {attempt + 1}/3", flush=True)
            time.sleep(5)
    try:
        _write_json(connection_status, {"status": "connected",
                                       "checked_at_utc": datetime.now(timezone.utc).isoformat()}, budget)
        result = fill_gaps(client, ROOT, config["history_start"], args.end, budget,
                           progress=lambda message: print(message, flush=True))
        print(json.dumps({key: value for key, value in result.items()
                          if key not in {"raw_sources", "failures", "unresolved"}}, indent=2))
        print(f"Unresolved stocks: {len(result['unresolved'])}; failed requests: {len(result['failures'])}")
        if result["unresolved"] or result["failures"]:
            raise SystemExit("Review data/raw/baostock_supplement/manifest.json; no active data switch.")
    finally:
        client.__exit__()


if __name__ == "__main__":
    with data_lock(ROOT / "data/state/market_download.lock"):
        main()
