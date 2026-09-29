"""Refresh latest daily signals, or rebuild the complete CSMAR-primary research."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_lab.research.current_check import run_current_check
from quant_lab.utils.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/research.yaml")
    parser.add_argument("--full-research", action="store_true", help="Refresh the entire historical pool and rebuild factors/backtests")
    args = parser.parse_args()
    config_path = ROOT / args.config
    result = run_current_check(ROOT, config_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "ready":
        raise SystemExit(1)
    if args.full_research:
        end = result["data_through"]
        index = load_config(config_path)["universe"].get("preferred_index", "000905.SH")
        for script, options in [
            ("fill_baostock_gaps.py", ["--end", end]),
            ("build_market_dataset.py", ["--refresh-staging-through", end, "--index", index]),
            ("run_factor_suite.py", ["--config", args.config]),
        ]:
            subprocess.run([sys.executable, "-X", "utf8", "-u", str(ROOT / "scripts" / script), *options], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
