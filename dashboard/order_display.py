"""Readable order previews; preserve the original machine-readable export."""

from __future__ import annotations

import pandas as pd

ORDER_LABELS = {
    "status": "状态", "signal_date": "信号日期", "planned_trade_date": "计划交易日",
    "symbol": "股票代码", "side": "方向", "current_shares": "当前股数",
    "target_shares": "目标股数", "shares": "拟交易股数", "reference_close": "参考收盘价",
    "target_weight": "目标权重", "factor_rank": "因子排名", "reason": "原因",
    "estimated_price": "估算成交价", "estimated_fee": "估算费用（元）",
    "estimated_tax": "估算税费（元）", "estimated_cash_after": "估算剩余现金（元）",
    "unfilled_shares": "无法成交股数", "conditional_on_sells": "依赖卖出成交",
}


def order_preview(orders: pd.DataFrame) -> pd.DataFrame:
    """Translate display labels and compact no-trade rows without mutating exports."""
    frame = orders.copy()
    if "status" in frame and not frame.empty:
        if frame["status"].eq("NO_TRADE").all():
            frame = frame[[c for c in ("status", "signal_date", "planned_trade_date", "reason") if c in frame]]
        frame["status"] = frame["status"].replace({
            "NO_TRADE": "无需调仓", "REVIEW_REQUIRED": "需人工复核", "UNFILLED": "无法成交",
        })
    if "side" in frame:
        frame["side"] = frame["side"].replace({"BUY": "买入", "SELL": "卖出", "buy": "买入", "sell": "卖出"})
    if "reason" in frame:
        frame["reason"] = frame["reason"].replace({
            "Not a W-FRI rebalance boundary": "尚未到周频调仓时点",
            "Not a M rebalance boundary": "尚未到月频调仓时点",
        })
    if "conditional_on_sells" in frame:
        frame["conditional_on_sells"] = frame["conditional_on_sells"].replace({True: "是，先核对卖单成交", False: "否"})
    return frame.rename(columns=ORDER_LABELS)
