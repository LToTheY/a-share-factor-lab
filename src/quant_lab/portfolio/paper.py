"""Human-reviewable paper-account state and next-session order proposals."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ORDER_COLUMNS = [
    "status",
    "signal_date",
    "planned_trade_date",
    "symbol",
    "side",
    "current_shares",
    "target_shares",
    "shares",
    "reference_close",
    "target_weight",
    "factor_rank",
    "reason",
    "estimated_price", "estimated_fee", "estimated_tax", "estimated_cash_after",
    "unfilled_shares", "conditional_on_sells",
]


def load_or_create_paper_state(path: str | Path, initial_cash: float) -> dict[str, Any]:
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if not state_path.exists():
        state_path.write_text(
            json.dumps(
                {"as_of_date": None, "cash": initial_cash, "positions": {}},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.setdefault("cash", initial_cash)
    state.setdefault("positions", {})
    return state


def is_rebalance_due(
    signal_date: pd.Timestamp,
    next_trade_date: pd.Timestamp,
    frequency: str,
) -> bool:
    if frequency.upper() == "D":
        return True
    return signal_date.to_period(frequency) != next_trade_date.to_period(frequency)


def build_next_day_orders(
    latest_signals: pd.DataFrame,
    latest_targets: pd.DataFrame,
    state: dict[str, Any],
    next_trade_date: str | pd.Timestamp | None,
    frequency: str,
    lot_size: int = 100,
    reference_prices: pd.DataFrame | None = None,
    costs=None,
) -> pd.DataFrame:
    """Create proposals only; never mutate holdings or claim future execution."""
    if latest_signals.empty:
        raise ValueError("Latest signal table is empty")
    signal_date = pd.Timestamp(latest_signals["trade_date"].max())
    if next_trade_date is None:
        return pd.DataFrame(
            [
                {
                    "status": "CALENDAR_UNKNOWN",
                    "signal_date": signal_date,
                    "reason": "Next exchange trading date is unavailable; do not trade",
                }
            ],
            columns=ORDER_COLUMNS,
        )
    next_date = pd.Timestamp(next_trade_date)
    if next_date <= signal_date:
        raise ValueError("计划交易日必须晚于信号日")
    if not is_rebalance_due(signal_date, next_date, frequency):
        return pd.DataFrame(
            [
                {
                    "status": "NO_TRADE",
                    "signal_date": signal_date,
                    "planned_trade_date": next_date,
                    "reason": f"Not a {frequency} rebalance boundary",
                }
            ],
            columns=ORDER_COLUMNS,
        )

    close_map = latest_signals.set_index("symbol")["close"].to_dict()
    if reference_prices is not None:
        required = {"symbol", "close"}
        missing_columns = required.difference(reference_prices.columns)
        if missing_columns:
            raise ValueError(
                "reference_prices is missing columns: "
                + ", ".join(sorted(missing_columns))
            )
        close_map.update(reference_prices.set_index("symbol")["close"].to_dict())
    from quant_lab.portfolio.order_plan import validate_account
    state = validate_account(state)
    positions = state["positions"]
    missing_prices = sorted(
        symbol
        for symbol, shares in positions.items()
        if shares
        and (
            symbol not in close_map
            or not np.isfinite(float(close_map[symbol]))
            or float(close_map[symbol]) <= 0
        )
    )
    if missing_prices:
        return pd.DataFrame(
            [
                {
                    "status": "PRICE_MISSING",
                    "signal_date": signal_date,
                    "planned_trade_date": next_date,
                    "reason": (
                        "Cannot value existing positions; update prices or paper state: "
                        + ", ".join(missing_prices)
                    ),
                }
            ],
            columns=ORDER_COLUMNS,
        )
    from quant_lab.backtest.engine import BacktestConfig
    from quant_lab.portfolio.order_plan import estimate_orders
    config = costs or BacktestConfig(lot_size=lot_size)
    rows = estimate_orders(latest_targets, state, close_map, signal_date, next_date, config)
    return pd.DataFrame(rows, columns=ORDER_COLUMNS)
