"""Runnable teaching example; hypothesis, not a claim of predictive ability."""

TITLE = "20日收盘位置"
DESCRIPTION = "观察近期收盘价在日内高低区间中的位置，检验买卖力量的持续性。"
DIRECTION = 1
LOOKBACK = 20
REQUIRED_COLUMNS = ["adj_high", "adj_low", "adj_close"]


def compute(frame):
    span = (frame.adj_high - frame.adj_low).replace(0, float("nan"))
    position = (frame.adj_close - frame.adj_low) / span
    return position.groupby(frame.symbol, sort=False).transform(
        lambda series: series.rolling(20, min_periods=20).mean()
    )
