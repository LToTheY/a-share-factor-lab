"""Run the complete workflow on deterministic synthetic data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_lab.pipeline import run_demo


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="reports/generated/demo")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--symbols", type=int, default=40)
    parser.add_argument("--days", type=int, default=700)
    args = parser.parse_args()
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    summary = run_demo(output, args.seed, args.symbols, args.days)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Artifacts: {output}")


if __name__ == "__main__":
    main()
