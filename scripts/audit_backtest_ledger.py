"""Independently reconcile saved experiment fills, cash, shares and valuations."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_lab.backtest.audit import audit_ledger
from quant_lab.dashboard.experiments import ExperimentStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--experiment")
    group.add_argument("--all", action="store_true", help="Include historical and failed-to-read experiment records")
    args = parser.parse_args()
    store = ExperimentStore(ROOT / "data/state/strategy_experiments")
    if args.all:
        records, errors = store.list()
    else:
        records, errors = [store.metadata(args.experiment)], []
    results = []
    for record in records:
        try:
            result = audit_ledger(*(store.table(record["id"], name) for name in ("equity", "trades", "positions")), record["backtest"]["initial_cash"])
            results.append({"experiment_id": record["id"], "name": record["name"],
                            "code_version": record["provenance"].get("code_version"), **result})
        except (ValueError, OSError, TypeError, KeyError) as exc:
            results.append({"experiment_id": record["id"], "passed": False, "error": str(exc)})
    now = datetime.now(timezone.utc)
    output = ROOT / "data/state/ledger_audits" / (now.strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {"created_at": now.isoformat(), "passed": bool(results) and not errors and all(r["passed"] for r in results),
              "experiments": results, "read_errors": errors}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "experiments": len(results),
                      "failed": sum(not r["passed"] for r in results), "report": str(output.relative_to(ROOT))}, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
