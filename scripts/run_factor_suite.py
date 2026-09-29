"""Run the same versioned research service used by the dashboard."""
import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from quant_lab.data.process_lock import data_lock
from quant_lab.research.jobs import JobStore
from quant_lab.research.service import (
    ResearchRequest,
    create_demo,
    local_path,
    run_research,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/research.yaml")
    parser.add_argument("--output")
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    config = create_demo(ROOT) if args.demo else yaml.safe_load((ROOT / args.config).read_text(encoding="utf-8"))
    request = ResearchRequest.create(ROOT, config)
    if args.background:
        print(json.dumps(JobStore(ROOT).submit("research", asdict(request)), ensure_ascii=False))
        return
    destination = local_path(ROOT, args.output or ("reports/generated/demo" if args.demo else config["project"]["output_dir"]), area="reports")
    candidate = destination.with_name(".pending-" + uuid4().hex)
    with data_lock(ROOT / "data/state/jobs/heavy.lock"), data_lock(ROOT / "data/state/market_download.lock"):
        summary = run_research(ROOT, request, candidate, print)
        if destination.exists():
            archive = ROOT / "reports/archive" / (destination.name + "_" + uuid4().hex)
            archive.parent.mkdir(parents=True, exist_ok=True)
            destination.rename(archive)
        try:
            candidate.rename(destination)
        except OSError:
            if not destination.exists() and "archive" in locals():
                archive.rename(destination)
            raise
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
