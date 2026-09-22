# 架构与学习地图

```text
BaoStock免费接口（2015年至今）
  ├── 不复权OHLC ───────────────┐
  ├── 后复权OHLC ───────────┐   │
  ├── 交易日历             │   │
  └── 历史中证500周度快照   │   │
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
         ├── 覆盖率门槛、年度/近期稳定性
         ├── 至少8个有效因子的等权综合分
         ├── Top 20 / Rank 30缓冲目标
         ├── 5年训练+1年验证+1年测试滚动回测
         └── latest_signal + next_day_orders
             │
             └── factor_scores.parquet
                       ▼
                 ml_study.py（独立研究，不进入每日信号）
                   ├── 等权 / Ridge / LightGBM / 可选MLP
                   ├── 5年训练+1年验证+1年测试+5日隔离
                   ├── 每折模型、样本外预测和特征稳定性
                   └── 含成本/无成本组合及预设敏感性
```

配置唯一入口是`configs/research.yaml`。选股数量只从这里读取，默认每天计算，
周频决定是否调仓；将`rebalance_frequency`改为`D`即可研究日频缓冲调仓。

机器学习研究已经形成独立可运行主线，但仍不进入当前每日纸面信号。只有模型在多折
样本外、交易成本和组合稳定性上持续改善，才考虑替换综合分。当前Ridge和LightGBM都
未在周频成本后战胜诊断基准，因此不升级为实时信号。
