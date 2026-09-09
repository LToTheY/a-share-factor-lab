"""Audit a canonical market file and exit non-zero on blocking errors."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_lab.data.audit import audit_daily_data, write_audit_report
from quant_lab.data.storage import read_table


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="data/processed/daily.parquet")
    parser.add_argument("--output", default="reports/generated/data_audit")
    args = parser.parse_args()
    result = audit_daily_data(read_table(ROOT / args.data))
    output = ROOT / args.output
    write_audit_report(result, output)
    print(json.dumps(result.summary, ensure_ascii=False, indent=2))
    print(f"Audit report: {output / 'audit.md'}")
    if result.has_errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
