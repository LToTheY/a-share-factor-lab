"""Replace the local paper-account cash and positions after manual fills."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_lab.research.settings import load_research_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/research.yaml")
    parser.add_argument("--cash", type=float, required=True)
    parser.add_argument(
        "--position",
        action="append",
        default=[],
        metavar="SYMBOL=SHARES",
        help="Repeat for each holding, for example 600000.SH=1000",
    )
    parser.add_argument("--as-of", default=None, help="YYYY-MM-DD")
    args = parser.parse_args()
    if args.cash < 0:
        raise SystemExit("cash cannot be negative")
    positions: dict[str, int] = {}
    for item in args.position:
        symbol, raw_shares = item.split("=", maxsplit=1)
        shares = int(raw_shares)
        if shares < 0:
            raise SystemExit("shares cannot be negative")
        if shares:
            positions[symbol.upper()] = shares
    settings = load_research_settings(ROOT / args.config)
    path = ROOT / settings.paper_state_file
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {"as_of_date": args.as_of, "cash": args.cash, "positions": positions}
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Paper account updated: {path}")


if __name__ == "__main__":
    main()
