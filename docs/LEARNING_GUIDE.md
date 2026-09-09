# 学习入口：从能运行到能面试解释

你不需要先通读全部代码。每次学习只完成“运行—观察—解释—修改—测试”五步。

## 第0课：先更新真实数据，只看结果

```powershell
.\scripts\daily_update.ps1
```

打开：

1. `reports/generated/daily_factor_lab/REPORT.md`
2. `reports/generated/daily_factor_lab/run_status.json`
3. `data/processed/baostock_daily_audit/audit.md`
4. `reports/generated/daily_factor_lab/latest_signal.csv`
5. `reports/generated/daily_factor_lab/next_day_orders.csv`

第一次建立自2015年起的长历史库，2015年作为预热、2016年起正式评价；以后只补本地
缺失日期。你需要先回答：这份结果使用了什么数据、哪一天形成信号、哪一天成交？还要
能解释历史成分股快照为什么比“用今天的500只股票回填十年”更可靠，以及周度快照仍有
什么误差。合成数据只保留给自动测试，不是学习主入口。

## 第1课：一行行情代表什么

阅读：

- `docs/DATA_CONTRACT.md`
- `src/quant_lab/data/schema.py`
- `src/quant_lab/data/audit.py`

练习：随机选一行，解释原始`close`、研究用`adj_close`、成交量、成交额、状态已知字段
和主键。说明为什么原始价用于股数，而后复权价用于因子。

面试问题：为什么“状态未知”不能直接填成False？

## 第2课：MOM_60_5如何计算

阅读：

- `docs/RESEARCH_SPEC.md`
- `src/quant_lab/factors/library.py`中的`momentum`
- `tests/test_factors.py`

练习：把`lookback=60, skip=5`改为`120, 20`，先预测缺失行数和因子方向，再运行测试。

面试问题：为什么所有`shift`必须按`symbol`分组？为什么跳过最近5日？

## 第3课：截面预处理

阅读：

- `src/quant_lab/evaluation/preprocess.py`
- `tests/test_preprocess.py`

练习：比较关闭和开启行业/市值中性化后的年度RankIC。

面试问题：中性化为什么可能同时降低收益和提高因子纯度？

## 第4课：单因子评价与因子相关性

阅读：

- `src/quant_lab/evaluation/diagnostics.py`
- 输出`factor_summary.csv`、`factor_correlation.csv`和`factor_scores.parquet`

练习：找出相关性最高的一对因子，解释同时加入它们是否真的增加了新信息。

面试问题：为什么不能看到全样本IC为负后直接翻转因子方向？

## 第5课：信号如何变成交易

阅读：

- `src/quant_lab/portfolio/weights.py`
- `src/quant_lab/portfolio/paper.py`
- `src/quant_lab/backtest/engine.py`
- `tests/test_backtest.py`

练习：比较`latest_signal.csv`、`target_weights.csv`和`next_day_orders.csv`，解释为什么
每天有排名但不一定有订单。

面试问题：跌停无法卖出时，为什么不能直接从持仓中删除？

## 第6课：稳健性和负结果

阅读`robustness.csv`，比较：

- 20/60/120日窗口
- 是否跳过近期收益
- 周频/月频
- 换手、成本、回撤和RankIC

练习：写一段不超过200字的结论，必须包含至少一个失效场景。

## 单因子毕业标准

你能够不看代码回答以下问题后，再进入多因子和机器学习：

- 数据何时可见，信号何时形成，交易何时执行？
- 如何避免幸存者偏差和未来数据？
- 为什么用RankIC而不是只看收益？
- 因子中性化改变了什么？
- 涨跌停、停牌和交易成本如何进入回测？
- 合成数据、样本内和样本外结果分别能说明什么？
