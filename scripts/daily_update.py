"""Update free data, audit, evaluate factors and write next-session proposals."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_lab.data.audit import audit_daily_data, write_audit_report
from quant_lab.data.baostock_client import BaoStockDownloader
from quant_lab.data.daily_update import (
    consolidate_incremental_panel,
    incremental_zz500_update,
)
from quant_lab.data.missing import classify_market_rows
from quant_lab.data.panel import fill_explicit_suspensions
from quant_lab.data.schema import validate_daily_frame
from quant_lab.data.storage import write_table
from quant_lab.portfolio.paper import build_next_day_orders, load_or_create_paper_state
from quant_lab.research.factor_suite import run_factor_suite
from quant_lab.research.live_ledger import record_live_snapshot
from quant_lab.research.settings import load_research_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/research.yaml")
    parser.add_argument("--end", default=None, help="YYYY-MM-DD; default is today")
    parser.add_argument("--initial-days", type=int, default=None)
    parser.add_argument(
        "--history-start",
        default=None,
        help="Explicit history start; defaults to config (2015-01-01)",
    )
    args = parser.parse_args()

    config_path = ROOT / args.config
    settings = load_research_settings(config_path)
    initial_days = args.initial_days or settings.initial_calendar_days
    raw_dir = ROOT / settings.raw_dir
    with BaoStockDownloader() as downloader:
        manifest = incremental_zz500_update(
            downloader,
            raw_dir,
            end_date=args.end,
            initial_calendar_days=initial_days,
            history_start_date=args.history_start or settings.history_start_date,
            membership_frequency=settings.membership_frequency,
        )
    expected_cache_symbols = manifest.get("historical_symbols", manifest["symbols"])
    complete_cache = (
        manifest["cached_adjusted_symbols"] == expected_cache_symbols
        and manifest["cached_raw_symbols"] == expected_cache_symbols
    )
    if not complete_cache or manifest["complete_through"] is None:
        raise SystemExit(
            "Raw/adjusted update is incomplete, so no report was produced. "
            "Run the same command again to resume from cached partitions."
        )
    if manifest["stale_trading_days"] > settings.max_stale_trading_days:
        raise SystemExit(
            f"Data are stale by {manifest['stale_trading_days']} trading days; "
            "the configured maximum is "
            f"{settings.max_stale_trading_days}. No signal was produced."
        )
    coverage = manifest["requested_end_coverage"] / manifest["symbols"]
    if (
        manifest["complete_through"] == manifest["requested_end"]
        and coverage < settings.required_latest_coverage
    ):
        raise SystemExit("Latest cross-section does not meet configured coverage")

    market = fill_explicit_suspensions(
        consolidate_incremental_panel(raw_dir, end_date=manifest["complete_through"])
    )
    market = classify_market_rows(market)
    market.attrs["provider"] = "baostock"
    market.attrs["universe"] = "zz500_local_point_in_time_snapshots"
    market.attrs["research_warning"] = manifest["survivorship_warning"]
    validate_daily_frame(market)
    processed = ROOT / settings.processed_file
    write_table(market, processed)

    calendar = pd.read_parquet(raw_dir / "trade_calendar.parquet")
    expected_dates = calendar.loc[
        calendar["is_trading_day"]
        & (calendar["trade_date"] >= market["trade_date"].min())
        & (calendar["trade_date"] <= market["trade_date"].max()),
        "trade_date",
    ]
    audit = audit_daily_data(
        market,
        expected_trade_dates=expected_dates,
        expected_latest_symbols=manifest["symbols"],
    )
    audit_dir = processed.parent / "baostock_daily_audit"
    write_audit_report(audit, audit_dir)
    if audit.has_errors:
        raise SystemExit(f"Strict audit failed; inspect {audit_dir / 'audit.md'}")

    output = ROOT / settings.output_dir
    summary, latest, targets = run_factor_suite(
        market,
        settings,
        output,
        next_trading_date=manifest["next_trade_date"],
    )
    state = load_or_create_paper_state(
        ROOT / settings.paper_state_file,
        float(settings.backtest["initial_cash"]),
    )
    latest_target_date = targets["trade_date"].max() if not targets.empty else None
    latest_targets = (
        targets[targets["trade_date"] == latest_target_date].copy()
        if latest_target_date is not None
        else targets
    )
    latest_market_date = pd.Timestamp(market["trade_date"].max())
    latest_reference_prices = market.loc[
        market["trade_date"] == latest_market_date, ["symbol", "close"]
    ]
    orders = build_next_day_orders(
        latest,
        latest_targets,
        state,
        manifest["next_trade_date"],
        settings.rebalance_frequency,
        lot_size=int(settings.backtest["lot_size"]),
        reference_prices=latest_reference_prices,
    )
    orders_path = ROOT / settings.next_orders_file
    orders_path.parent.mkdir(parents=True, exist_ok=True)
    orders.to_csv(orders_path, index=False, encoding="utf-8-sig")
    live_snapshot = record_live_snapshot(
        latest,
        orders,
        config_path,
        ROOT / "data" / "state" / "live_signals",
        settings.validation.get("live_start_date"),
    )
    (output / "effective_config.yaml").write_text(
        config_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    run_status = {
        "data": manifest,
        "audit_passed": True,
        "research": summary,
        "order_status": str(orders.iloc[0]["status"]),
        "live_snapshot": live_snapshot,
        "paper_state_file": settings.paper_state_file,
    }
    (output / "run_status.json").write_text(
        json.dumps(run_status, ensure_ascii=False, indent=2, allow_nan=True),
        encoding="utf-8",
    )
    print(json.dumps(run_status, ensure_ascii=False, indent=2, allow_nan=True))
    print(f"Research report: {output / 'REPORT.md'}")
    print(f"Latest signals: {output / 'latest_signal.csv'}")
    print(f"Next-session plan: {orders_path}")


if __name__ == "__main__":
    main()
