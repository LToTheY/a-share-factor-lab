# 架构与学习地图

```text
BaoStock免费接口
  ├── 不复权OHLC ───────────────┐
  ├── 后复权OHLC ───────────┐   │
  ├── 交易日历             │   │
  └── 中证500快照           │   │
             ▼             ▼   ▼
      daily_update.py → 双价格合并 → 严格数据审计
             │                 │
             │                 ├── 原始价 → 整手/费用/纸面订单
             │                 └── 后复权价 → 因子/标签/收益研究
             ▼
       universe/filters.py
             ▼
       factors/library.py（11个普通价量因子）
             ▼
       evaluation/preprocess.py（逐日去极值、标准化）
             ▼
       factor_suite.py
         ├── 单因子IC、分层和相关性
         ├── 方向固定后的等权综合分
         ├── Top 20 / Rank 30缓冲目标
         ├── 日频数据回测
         └── latest_signal + next_day_orders
```

配置唯一入口是`configs/research.yaml`。选股数量只从这里读取，默认每天计算，
周频决定是否调仓；将`rebalance_frequency`改为`D`即可研究日频缓冲调仓。

机器学习和深度学习目录仍保留，但不进入当前每日主流程。先把普通因子数据时点、交易
规则和纸面验证跑稳，再把综合分替换成只在训练窗口拟合的模型预测。
