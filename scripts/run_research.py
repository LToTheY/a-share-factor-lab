"""Run one factor study and portfolio backtest on a canonical market file."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_lab.backtest.engine import BacktestConfig, run_backtest
from quant_lab.backtest.metrics import (
    performance_metrics,
    turnover_from_trades,
)
from quant_lab.data.storage import read_table
from quant_lab.evaluation.diagnostics import (
    information_coefficient,
    quantile_returns,
    summarize_ic,
)
from quant_lab.pipeline import prepare_factor
from quant_lab.portfolio.weights import top_n_weights
from quant_lab.universe.filters import UniverseConfig, apply_universe


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="data/processed/daily.parquet")
    parser.add_argument("--factor", default="momentum_60_5")
    parser.add_argument("--forward", type=int, default=5)
    parser.add_argument("--top-n", type=int, default=50)
    parser.add_argument("--output", default="reports/generated/real_data")
    args = parser.parse_args()

    market = read_table(ROOT / args.data)
    market = apply_universe(
        market,
        UniverseConfig(min_listed_days=120, exclude_st=True, min_amount=10_000_000),
    )
    research = prepare_factor(market, args.factor, args.forward)
    return_col = f"forward_return_{args.forward}d"
    ic = information_coefficient(research, "factor_processed", return_col)
    groups = quantile_returns(research, "factor_processed", return_col)
    weights = top_n_weights(research, top_n=args.top_n)
    result = run_backtest(market, weights, BacktestConfig())
    metrics = performance_metrics(result.equity)
    metrics["turnover"] = turnover_from_trades(result.trades, result.equity)
    summary = {"factor": summarize_ic(ic), "portfolio": metrics}

    output = ROOT / args.output
    output.mkdir(parents=True, exist_ok=True)
    research.to_csv(output / "factor_research.csv", index=False, encoding="utf-8-sig")
    groups.to_csv(output / "quantile_returns.csv", index=False)
    result.equity.to_csv(output / "equity.csv", index=False)
    result.trades.to_csv(output / "trades.csv", index=False)
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Artifacts: {output}")


if __name__ == "__main__":
    main()
