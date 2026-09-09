"""Generate the complete stage-3 MOM_60_5 study and report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_lab.data.storage import read_table
from quant_lab.data.synthetic import make_synthetic_daily_data
from quant_lab.research.momentum import run_momentum_study
from quant_lab.research.settings import load_research_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["synthetic", "real"], default="real")
    parser.add_argument("--config", default="configs/research.yaml")
    parser.add_argument("--data", default="data/processed/baostock_daily.parquet")
    parser.add_argument("--output", default=None)
    parser.add_argument("--allow-audit-errors", action="store_true")
    parser.add_argument("--symbols", type=int, default=60)
    parser.add_argument("--days", type=int, default=800)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    settings = load_research_settings(ROOT / args.config)

    if args.mode == "synthetic":
        market = make_synthetic_daily_data(args.symbols, args.days, seed=args.seed)
        default_output = ROOT / "reports" / "generated" / "momentum_synthetic"
    else:
        market = read_table(ROOT / args.data)
        market.attrs["provider"] = "baostock"
        market.attrs["universe"] = "historical_zz500_weekly_snapshots"
        market.attrs["research_warning"] = (
            "Historical CSI 500 membership is sampled weekly; dates between snapshots "
            "use the latest membership snapshot known at that date."
        )
        default_output = ROOT / "reports" / "generated" / "momentum_real_daily"
    output = Path(args.output) if args.output else default_output
    if not output.is_absolute():
        output = ROOT / output
    summary = run_momentum_study(
        market,
        output,
        strict_audit=not args.allow_audit_errors,
        top_n=settings.top_n,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Report: {output / 'REPORT.md'}")


if __name__ == "__main__":
    main()
