# 架构与学习地图

2026-09-29新增当期检查链路：`daily_update.py`默认调用`current_check.py`，按北京时间
联网核验交易日历、当前股票池和日行情；CSMAR原始值优先、BaoStock补缺、统一复权，
通过窗口完整性及因子覆盖检查后发布最新纸面建议。状态与报告独立保存，详见
[每日流程](DAILY_WORKFLOW.md)。完整历史研究也已切换到国泰安优先的新库；文件与口径见
[当前数据使用说明](DATA_USAGE.md)。

```text
CSMAR + BaoStock补充（2015年至今）
  ├── 不复权OHLC ───────────────┐
  ├── 昨收链统一复权 ──────┐   │
  ├── 交易日历             │   │
  └── 沪深300/中证500历史快照│   │
             ▼             ▼   ▼
      daily_update.py --full-research → 双价格合并 → 严格数据审计
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

每日流程配置入口是`configs/research.yaml`。网页实验保存独立完整请求，不覆盖每日配置；默认每天计算，
周频决定是否调仓；将`rebalance_frequency`改为`D`即可研究日频缓冲调仓。

机器学习研究已经形成独立可运行主线，但仍不进入当前每日纸面信号。只有模型在多折
样本外、交易成本和组合稳定性上持续改善，才考虑替换综合分。当前Ridge和LightGBM都
未在周频成本后战胜诊断基准，因此不升级为实时信号。


## 统一研究服务（2026-09-30）

`research/service.py` 负责请求快照、行情哈希、字段/预热/股票池、因子研究与结果清单。
`research/jobs.py` 负责进程锁、去重、持久状态、协作取消和候选结果发布。
`research/strategy_service.py` 绑定源报告，必要时按新股票池重算截面，再执行策略和验证。
`strategy/validation.py` 固定实验参数进行时序分折及有限单因素对照，不按测试结果自动选优。
Streamlit只收集请求和展示结果；CLI复用同一服务，不提供公开网络API。

成功研究：`reports/generated/jobs/<任务编号>/`；任务状态：`data/state/jobs/`；
实验：`data/state/strategy_experiments/`。旧结果不会因重跑失败而被覆盖。
