"""Build and strictly audit the free BaoStock CSI 500 daily panel."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_lab.data.audit import audit_daily_data, write_audit_report
from quant_lab.data.panel import fill_explicit_suspensions
from quant_lab.data.schema import validate_daily_frame
from quant_lab.data.storage import write_table
from quant_lab.universe.filters import attach_index_membership


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="data/raw/baostock")
    parser.add_argument(
        "--output", default="data/processed/baostock_zz500_daily.parquet"
    )
    args = parser.parse_args()
    source = ROOT / args.input
    files = sorted((source / "daily").glob("*.parquet"))
    if not files:
        raise SystemExit(f"No symbol partitions found under {source / 'daily'}")
    market = fill_explicit_suspensions(
        pd.concat((pd.read_parquet(path) for path in files), ignore_index=True)
    )
    membership_path = source / "zz500_membership.parquet"
    if not membership_path.exists():
        raise SystemExit(f"Missing historical membership: {membership_path}")
    market = attach_index_membership(market, pd.read_parquet(membership_path))
    validation = validate_daily_frame(market)
    calendar_path = source / "trade_calendar.parquet"
    expected_dates = None
    if calendar_path.exists():
        calendar = pd.read_parquet(calendar_path)
        expected_dates = calendar.loc[calendar["is_trading_day"], "trade_date"]
    audit = audit_daily_data(market, expected_trade_dates=expected_dates)
    output = ROOT / args.output
    write_table(market, output)
    write_audit_report(audit, output.parent / "baostock_audit")
    print(
        f"Wrote {validation.rows:,} rows / {validation.symbols:,} symbols to {output}"
    )
    print(f"Strict audit passed: {not audit.has_errors}")
    if audit.has_errors:
        raise SystemExit("Audit failed; inspect data/processed/baostock_audit/audit.md")


if __name__ == "__main__":
    main()
