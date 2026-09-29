# 普通因子研究入口

## 默认因子矩阵

| 因子 | 含义 | 默认方向 |
|---|---|---:|
| momentum_20_5 | 20日至5日前动量 | +1 |
| momentum_60_5 | 60日至5日前动量 | +1 |
| momentum_120_20 | 120日至20日前动量 | +1 |
| reversal_5 | 5日反转 | +1 |
| reversal_20 | 20日反转 | +1 |
| volatility_20 | 20日波动率 | -1 |
| volatility_60 | 60日波动率 | -1 |
| amihud_20 | 20日非流动性 | -1 |
| turnover_mean_20 | 20日平均换手 | -1 |
| amount_momentum_20 | 近期成交额相对20日均值 | +1 |
| price_volume_corr_20 | 收益与成交额变化的20日相关性 | +1 |

另外提供三个可选候选，不自动加入默认综合分：

| 因子 | 含义 | 研究方向假设 |
|---|---|---:|
| downside_volatility_20 | 20日负收益平方均值的平方根 | -1 |
| overnight_reversal_5 | 最近5日隔夜收益均值取负 | +1 |
| intraday_momentum_20 | 最近20日开盘到收盘收益均值 | +1 |

这些是待检验的机制假设。`intraday_momentum_20` 使用日线开收盘价，并非分钟级策略。
计算公式、输入与最低有效天数见 [研究方法](FACTOR_RESEARCH_METHOD.md)。

`direction`是事先写入配置的研究假设，不是看完整样本结果后翻转符号。每个因子先按日
去极值、标准化，再乘方向并等权平均。免费数据没有严格时点市值和历史行业，所以当前
默认关闭市值与行业中性化。

## 每次运行产生什么

- `factor_summary.csv`：每个因子的RankIC、ICIR、胜率、分组差，以及次日开盘口径IC和HAC均值检验。
- `execution_ic_<因子>.csv`：信号后下一交易日开盘到后续开盘的日度RankIC。
- `annual_ic_<因子>.csv`：按年度拆分的IC，用于观察稳定性；当年不足一整年时需单独说明。
- `factor_correlation.csv`：因子之间的平均日度截面相关性，用于识别重复因子。
- `factor_scores.parquet`：完整日频因子矩阵和综合分。
- `latest_signal.csv`：最新交易日全部候选股票及综合排名。
- `target_weights.csv`：仅在配置的调仓日形成的Top 20缓冲目标。

只修改因子或组合配置、无需重新联网下载时运行：

```powershell
.\.venv\Scripts\python.exe scripts\run_factor_suite.py
```

## 如何加入自己的因子

推荐使用新的 Python 自定义因子入口：在 `src/quant_lab/factors/custom/` 保存 `.py`
文件，然后在前端“因子库 → 日频 → 更新代码因子”点击加载。
页面内有完整教程、可复制/下载模板、独立研究配置和研究结果切换入口。
详见 [自定义因子教程](CUSTOM_FACTORS.md)。下面保留内置库的维护方法。

1. 在`src/quant_lab/factors/library.py`写函数，必须先按`symbol`分组再rolling/shift。
2. 加到`FACTOR_REGISTRY`。
3. 在`src/quant_lab/research/service.py`的`BUILTIN_INPUTS`声明真实输入字段和所需历史长度。
4. 在`configs/research.yaml`的`definitions`加入名称和事先约定的方向，或从网页选入独立研究。
5. 添加测试，至少检查股票之间不串值、窗口缺失期、输入排序和追加未来数据不改变过去值。
6. 运行`scripts/run_factor_suite.py`，用已保存的历史数据检查因子IC、分层、换手和相关性，不只看回测收益。`daily_update.ps1`用于最新行情与当期纸面调仓检查，不重建完整历史报告。

【面试高频】不能在全样本看到IC为负后直接把因子乘以-1，再声称样本外有效。因子方向
必须来自经济假设或只在训练窗口确定，然后在后续时间窗口冻结验证。
