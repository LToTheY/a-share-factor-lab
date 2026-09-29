"""Fee-aware, reference-price estimates for a manually reviewed account."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from quant_lab.backtest.engine import (
    BacktestConfig,
    _commission,
    _stamp_duty_rate,
    affordable_quantity,
)
from quant_lab.backtest.lot_rules import round_order


def validate_account(state: dict) -> dict:
    if not isinstance(state, dict) or not isinstance(state.get("positions", {}), dict):
        raise TypeError("账户应包含 cash 和 positions 字典")
    if isinstance(state.get("cash"), bool):
        raise TypeError("可用现金必须是金额，不能是布尔值")
    cash = float(state.get("cash", 0))
    if not math.isfinite(cash) or cash < 0:
        raise ValueError("可用现金必须为非负有限数值")
    positions = {}
    for symbol, value in state.get("positions", {}).items():
        if isinstance(value, bool):
            raise TypeError(f"持仓股数不能是布尔值：{symbol}")
        number = float(value)
        if not math.isfinite(number) or number < 0 or not number.is_integer():
            raise ValueError(f"持仓股数必须为非负整数：{symbol}")
        if number:
            positions[str(symbol)] = int(number)
    return {**state, "cash": cash, "positions": positions}


def estimate_orders(targets: pd.DataFrame, state: dict, prices: dict,
                    signal_date, trade_date, costs: BacktestConfig) -> list[dict]:
    state = validate_account(state)
    positions = state["positions"].copy()
    cash = state["cash"]
    required = set(positions) | set(targets.get("symbol", []))
    if any(not np.isfinite(float(prices.get(s, np.nan))) or float(prices.get(s, 0)) <= 0 for s in required):
        return [{"status": "PRICE_MISSING", "reason": "目标或持仓缺少有效参考价，禁止生成金额估算"}]
    target_map = targets.set_index("symbol").to_dict("index") if not targets.empty else {}
    weights = [float(t.get("target_weight", 0)) for t in target_map.values()]
    if any(not math.isfinite(w) or w < 0 for w in weights) or sum(weights) > 1 + 1e-9:
        raise ValueError("目标权重无效或超过100%")
    equity = cash + sum(q * float(prices[s]) for s, q in positions.items())
    desired = {s: round_order(equity * target_map.get(s, {}).get("target_weight", 0) / float(prices[s]), s, costs.lot_size) for s in required}
    rows = []
    sold = False
    # These are conditional reference estimates. Tomorrow's blocked sells must
    # cause the user to recompute buys, never count unsettled hypothetical cash.
    for side in ("SELL", "BUY"):
        ordered = sorted(required, key=lambda s: (target_map.get(s, {}).get("factor_rank", np.inf), s))
        for symbol in ordered:
            current = positions.get(symbol, 0)
            delta = desired[symbol] - current
            if (side == "SELL" and delta >= 0) or (side == "BUY" and delta <= 0):
                continue
            requested = round_order(abs(delta), symbol, costs.lot_size, holding=current if side == "SELL" else None)
            price = float(prices[symbol]) * (1 + costs.slippage_bps/10000 * (1 if side == "BUY" else -1))
            quantity = affordable_quantity(requested, price, cash, symbol, costs, trade_date) if side == "BUY" else requested
            fee = _commission(quantity * price, costs, trade_date) if quantity else 0.
            tax = quantity * price * _stamp_duty_rate(pd.Timestamp(trade_date), costs) if side == "SELL" else 0.
            reason = "按信号日价格估算；需人工核对实际报价、状态及资金"
            if side == "BUY" and sold:
                reason += "；买入预算以卖单先成功成交为条件"
            if quantity < abs(delta):
                reason += "；最低申报量或含费用现金限制，部分/全部未分配"
            if side == "SELL" and quantity * price + cash < fee + tax:
                quantity, fee, tax = 0, 0., 0.
                reason = "卖出收入与可用现金不足支付估算费用"
            gross = quantity * price
            cash += (gross if side == "SELL" else -gross) - fee - tax
            positions[symbol] = current + (quantity if side == "BUY" else -quantity)
            sold |= side == "SELL" and quantity > 0
            rows.append({"status": "REVIEW_REQUIRED" if quantity else "UNFILLED",
                         "signal_date": signal_date, "planned_trade_date": trade_date,
                         "symbol": symbol, "side": side, "current_shares": current,
                         "target_shares": desired[symbol], "shares": quantity,
                         "reference_close": prices[symbol], "target_weight": target_map.get(symbol, {}).get("target_weight", 0),
                         "factor_rank": target_map.get(symbol, {}).get("factor_rank", np.nan),
                         "estimated_price": price, "estimated_fee": fee, "estimated_tax": tax,
                         "estimated_cash_after": cash, "unfilled_shares": abs(delta)-quantity,
                         "conditional_on_sells": side == "BUY" and sold, "reason": reason})
    if not rows:
        rows.append({"status": "NO_TRADE", "signal_date": signal_date, "planned_trade_date": trade_date,
                     "estimated_cash_after": cash,
                     "reason": "按参考价与最低申报量计算，无可执行调整；请同时查看选股筛选原因"})
    return rows
