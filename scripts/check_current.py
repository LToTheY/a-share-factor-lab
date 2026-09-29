"""Update current data, validate freshness, and prepare a paper rebalance check."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_lab.research.current_check import run_current_check


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/research.yaml")
    args = parser.parse_args()
    result = run_current_check(ROOT, ROOT / args.config)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "ready":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
