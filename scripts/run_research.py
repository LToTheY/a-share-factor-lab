"""Compatibility CLI for one factor, using the same research service as the UI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_lab.data.process_lock import data_lock
from quant_lab.research.service import ResearchRequest, local_path, run_research
from quant_lab.research.snapshots import snapshot_source


def configuration(args):
    config = yaml.safe_load((ROOT / args.config).read_text(encoding="utf-8"))
    if args.data:
        config["data"]["processed_file"] = args.data
    for key in ("start", "end"):
        if getattr(args, key):
            config["data"][f"research_{key}_date"] = getattr(args, key)
    definitions = {item["name"]: item.get("direction", 1.) for item in config["factors"]["definitions"]}
    direction = definitions.get(args.factor, 1.) if args.direction is None else args.direction
    config["factors"].update(definitions=[{"name": args.factor, "direction": direction}],
                             forward_periods=args.forward, minimum_valid_factors=1)
    config["portfolio"].update(top_n=args.top_n, exit_rank=max(args.top_n, config["portfolio"]["exit_rank"]),
                               max_weight=1 / args.top_n)
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/research.yaml")
    parser.add_argument("--data", help="Defaults to the canonical market file in the config")
    parser.add_argument("--factor", default="momentum_60_5")
    parser.add_argument("--direction", type=float, choices=(-1., 1.))
    parser.add_argument("--forward", type=int, default=5)
    parser.add_argument("--top-n", type=int, default=50)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--output", default="reports/generated/real_data")
    args = parser.parse_args()
    if args.top_n < 1 or args.forward < 1:
        parser.error("--top-n and --forward must be positive")
    request = ResearchRequest.create(ROOT, configuration(args))
    destination = local_path(ROOT, args.output, area="reports")
    candidate = destination.with_name(".pending-" + uuid4().hex)
    with data_lock(ROOT / "data/state/jobs/heavy.lock"), data_lock(ROOT / "data/state/market_download.lock"):
        snapshot_source(ROOT, request.code_version)
        summary = run_research(ROOT, request, candidate, print)
        archive = None
        if destination.exists():
            archive = ROOT / "reports/archive" / (destination.name + "_" + uuid4().hex)
            archive.parent.mkdir(parents=True, exist_ok=True)
            destination.rename(archive)
        try:
            candidate.rename(destination)
        except OSError:
            if archive is not None and not destination.exists():
                archive.rename(destination)
            raise
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("Shared output schema: factor_scores.parquet, factor_summary.csv, summary.json, dataset_provenance.json")


if __name__ == "__main__":
    main()
