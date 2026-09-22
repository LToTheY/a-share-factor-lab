"""Run leakage-aware ML walk-forward research on cached real factor scores."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_lab.models.settings import SUPPORTED_MODELS, load_ml_settings
from quant_lab.research.ml_study import run_ml_study
from quant_lab.research.settings import load_research_settings


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/ml_research.yaml")
    parser.add_argument(
        "--models",
        nargs="+",
        choices=sorted(SUPPORTED_MODELS),
        default=None,
        help="Override configured models, for example: --models ridge lightgbm",
    )
    args = parser.parse_args()

    config_path = ROOT / args.config
    settings = load_ml_settings(config_path)
    research_settings = load_research_settings(ROOT / settings.research_config)
    scores_path = ROOT / settings.scores_file
    market_path = ROOT / settings.market_file
    if not scores_path.exists():
        raise SystemExit(
            f"Missing factor scores: {scores_path}. Run scripts/run_factor_suite.py first."
        )
    if not market_path.exists():
        raise SystemExit(
            f"Missing processed market data: {market_path}. Run daily_update.ps1 first."
        )

    scores = pd.read_parquet(scores_path)
    scores.attrs["provider"] = "baostock_cached_factor_scores"
    market = pd.read_parquet(market_path)
    manifest_path = ROOT / research_settings.raw_dir / "update_manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {}
    )
    output = ROOT / settings.output_dir
    summary = run_ml_study(
        scores,
        market,
        settings,
        research_settings,
        output,
        enabled_models=tuple(args.models) if args.models else None,
        next_trading_date=manifest.get("next_trade_date"),
    )
    (output / "effective_ml_config.yaml").write_text(
        config_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    effective_models = tuple(args.models) if args.models else settings.enabled_models
    research_config_path = ROOT / settings.research_config
    run_manifest = {
        "models": list(effective_models),
        "ml_config": str(config_path.relative_to(ROOT)),
        "ml_config_sha256": _sha256(config_path),
        "research_config": str(research_config_path.relative_to(ROOT)),
        "research_config_sha256": _sha256(research_config_path),
        "scores_file": str(scores_path.relative_to(ROOT)),
        "scores_size": scores_path.stat().st_size,
        "scores_modified_ns": scores_path.stat().st_mtime_ns,
        "market_file": str(market_path.relative_to(ROOT)),
        "market_size": market_path.stat().st_size,
        "market_modified_ns": market_path.stat().st_mtime_ns,
    }
    (output / "run_manifest.json").write_text(
        json.dumps(run_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=True))
    print(f"ML report: {output / 'REPORT.md'}")


if __name__ == "__main__":
    main()
