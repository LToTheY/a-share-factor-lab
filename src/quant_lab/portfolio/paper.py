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
    positions = {str(key): int(value) for key, value in state["positions"].items()}
    account_value = float(state["cash"]) + sum(
        shares * float(close_map.get(symbol, 0.0))
        for symbol, shares in positions.items()
    )
    target_map = latest_targets.set_index("symbol").to_dict("index")
    rows = []
    for symbol in sorted(set(positions) | set(target_map)):
        current = positions.get(symbol, 0)
        target = target_map.get(symbol, {})
        price = float(close_map.get(symbol, np.nan))
        weight = float(target.get("target_weight", 0.0))
        if weight and np.isfinite(price) and price > 0:
            target_shares = int(
                np.floor(account_value * weight / price / lot_size) * lot_size
            )
        else:
            target_shares = 0
        delta = target_shares - current
        if delta == 0:
            continue
        rows.append(
            {
                "status": "REVIEW_REQUIRED",
                "signal_date": signal_date,
                "planned_trade_date": next_date,
                "symbol": symbol,
                "side": "BUY" if delta > 0 else "SELL",
                "current_shares": current,
                "target_shares": target_shares,
                "shares": abs(delta),
                "reference_close": price,
                "target_weight": weight,
                "factor_rank": target.get("factor_rank", np.nan),
                "reason": "Check next-open price, suspension and price limit manually",
            }
        )
    if not rows:
        rows.append(
            {
                "status": "NO_TRADE",
                "signal_date": signal_date,
                "planned_trade_date": next_date,
                "reason": "Buffered target matches paper-account positions",
            }
        )
    return pd.DataFrame(rows, columns=ORDER_COLUMNS)
