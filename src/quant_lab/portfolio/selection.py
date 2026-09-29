"""Signal-close portfolio selection shared by backtests and manual proposals."""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant_lab.backtest.engine import BacktestConfig, _commission
from quant_lab.backtest.lot_rules import order_unit

TARGET_COLUMNS = ["trade_date", "symbol", "target_weight", "factor_processed", "factor_rank"]
AUDIT_COLUMNS = ["trade_date", "symbol", "factor_rank", "reference_close", "minimum_cost", "slot_budget", "selected", "reason"]


def board_allowed(symbol: str, *, allow_star: bool, allow_chinext: bool) -> bool:
    code = str(symbol).split(".")[0]
    return ((allow_star or not code.startswith(("688", "689")))
            and (allow_chinext or not code.startswith(("300", "301"))))


def select_targets(signals: pd.DataFrame, prices: pd.DataFrame, holdings: dict[str, int],
                   equity: float, spec, costs: BacktestConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Use only signal-day information, actual holdings and a declared slot budget.

    Affordable mode never enlarges a slot to force an order. It searches the same
    factor ranking for candidates whose minimum order plus estimated fees fits
    min(1/TopN, cap) * equity. Overnight gaps still can prevent execution.
    """
    date = pd.Timestamp(signals.trade_date.max()) if not signals.empty else pd.NaT
    ranked = signals.loc[np.isfinite(signals.factor_processed)].sort_values(
        ["factor_processed", "symbol"], ascending=[False, True], kind="stable"
    ).copy()
    ranked["factor_rank"] = np.arange(1, len(ranked) + 1)
    price_map = prices.set_index("symbol")["close"].to_dict()
    weight = min(1 / spec.top_n, spec.max_weight)
    slot = max(0.0, equity * weight * (1 - spec.cash_buffer))
    eligible, audit = [], []
    for row in ranked.itertuples(index=False):
        symbol = str(row.symbol)
        price = float(price_map.get(symbol, np.nan))
        minimum, _ = order_unit(symbol, costs.lot_size)
        gross = minimum * price * (1 + costs.slippage_bps / 10000)
        minimum_cost = gross + _commission(gross, costs, date) if np.isfinite(gross) and gross > 0 else np.nan
        held = holdings.get(symbol, 0) > 0
        if not board_allowed(symbol, allow_star=spec.allow_star, allow_chinext=spec.allow_chinext):
            reason = "未启用该板块交易权限"
        elif not np.isfinite(price) or price <= 0:
            reason = "缺少有效信号日价格"
        elif spec.selection_mode == "affordable" and not held and minimum_cost > slot + 1e-9:
            reason = "单股预算不足最低申报量及费用"
        else:
            reason = "排名候选"
            eligible.append(symbol)
        audit.append({"trade_date": date, "symbol": symbol, "factor_rank": row.factor_rank,
                      "reference_close": price, "minimum_cost": minimum_cost,
                      "slot_budget": slot, "selected": False, "reason": reason})
    ranked = ranked.set_index("symbol", drop=False)
    chosen = [s for s in eligible if holdings.get(s, 0) > 0 and ranked.at[s, "factor_rank"] <= spec.exit_rank][:spec.top_n]
    chosen += [s for s in eligible if s not in chosen][:spec.top_n - len(chosen)]
    if chosen:
        targets = ranked.loc[chosen, ["trade_date", "symbol", "factor_processed", "factor_rank"]].reset_index(drop=True)
        # Rank-only keeps the original equal-weight convention; affordable mode
        # reserves fixed slots and never increases risk when candidates are scarce.
        allocation = weight if spec.selection_mode == "affordable" else min(1 / len(chosen), spec.max_weight)
        targets["target_weight"] = allocation * (1 - spec.cash_buffer)
        targets = targets[TARGET_COLUMNS]
    else:
        targets = pd.DataFrame(columns=TARGET_COLUMNS)
    for row in audit:
        row["selected"] = row["symbol"] in chosen
        if row["selected"]:
            row["reason"] = "实际持仓在退出缓冲内" if holdings.get(row["symbol"], 0) > 0 else "进入目标组合"
        elif row["reason"] == "排名候选":
            row["reason"] = "本期名额已满"
    return targets, pd.DataFrame(audit, columns=AUDIT_COLUMNS)
