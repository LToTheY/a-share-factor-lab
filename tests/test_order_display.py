import pandas as pd

from dashboard.order_display import order_preview


def test_no_trade_preview_explains_schedule_and_preserves_export():
    orders = pd.DataFrame([{"status": "NO_TRADE", "signal_date": "2026-09-29",
                            "planned_trade_date": "2026-09-30", "shares": None,
                            "reason": "Not a W-FRI rebalance boundary"}])
    original = orders.copy(deep=True)
    preview = order_preview(orders)
    assert list(preview) == ["状态", "信号日期", "计划交易日", "原因"]
    assert preview.iloc[0]["原因"] == "尚未到周频调仓时点"
    pd.testing.assert_frame_equal(orders, original)


def test_trade_preview_retains_cash_and_sell_dependencies():
    orders = pd.DataFrame([{"status": "REVIEW_REQUIRED", "side": "BUY", "shares": 100,
                            "estimated_cash_after": 500.0, "conditional_on_sells": True}])
    preview = order_preview(orders)
    assert preview.iloc[0]["方向"] == "买入"
    assert preview.iloc[0]["拟交易股数"] == 100
    assert preview.iloc[0]["估算剩余现金（元）"] == 500.0
    assert preview.iloc[0]["依赖卖出成交"] == "是，先核对卖单成交"


def test_unknown_order_fields_remain_visible():
    frame = pd.DataFrame([{"status": "BLOCKED", "reason": "unrecognized detail", "extra": 123}])
    preview = order_preview(frame)
    assert preview.iloc[0]["状态"] == "BLOCKED"
    assert preview.iloc[0]["原因"] == "unrecognized detail"
    assert preview.iloc[0]["extra"] == 123
