"""Combine cached Tushare daily partitions into one validated research table."""

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
    parser.add_argument("--input", default="data/raw/tushare/daily")
    parser.add_argument("--output", default="data/processed/daily.parquet")
    parser.add_argument(
        "--index-weights", default="data/raw/tushare/index_000905_SH.parquet"
    )
    args = parser.parse_args()
    source = ROOT / args.input
    files = sorted(source.glob("daily_*.parquet"))
    if not files:
        raise SystemExit(f"No daily_*.parquet files found under {source}")
    frame = fill_explicit_suspensions(
        pd.concat(map(pd.read_parquet, files), ignore_index=True)
    )
    index_path = ROOT / args.index_weights
    if index_path.exists():
        frame = attach_index_membership(frame, pd.read_parquet(index_path))
        print(f"Attached historical index membership from {index_path}")
    else:
        print(
            f"Index weights not found; dataset remains an all-A-share panel: {index_path}"
        )
    report = validate_daily_frame(frame)
    output = write_table(frame, ROOT / args.output)
    calendar_path = source.parent / "trade_calendar.parquet"
    expected_dates = None
    if calendar_path.exists():
        expected_dates = pd.read_parquet(calendar_path)["trade_date"]
    audit = audit_daily_data(frame, expected_trade_dates=expected_dates)
    write_audit_report(audit, (ROOT / args.output).parent / "audit")
    print(
        f"Wrote {report.rows:,} rows / {report.symbols:,} symbols "
        f"({report.start_date.date()} to {report.end_date.date()}) to {output}"
    )
    print(f"Audit passed: {not audit.has_errors}")
    if audit.has_errors:
        raise SystemExit(
            "Dataset written for inspection, but audit has blocking errors."
        )


if __name__ == "__main__":
    main()
