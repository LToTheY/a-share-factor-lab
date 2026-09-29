"""复制为 my_momentum.py；以下划线开头的文件不会被加载。"""

import pandas as pd

TITLE = "我的20日动量"
DESCRIPTION = "过去20个交易日的复权收益；仅为接口示例，不代表有效策略。"
DIRECTION = 1  # 事先约定：1 越大越好，-1 越小越好
LOOKBACK = 20  # 当前值之外需要的历史交易行数
REQUIRED_COLUMNS = ["adj_close"]


def compute(frame: pd.DataFrame) -> pd.Series:
    """输入已按 symbol、trade_date 排序；返回与 frame 同索引的数值 Series。"""
    price = frame["adj_close"]
    previous = price.groupby(frame["symbol"], sort=False).shift(LOOKBACK)
    # 不要 reset_index / 排序返回值，不要使用 shift(-1) 或未来收益标签。
    return price / previous - 1.0
