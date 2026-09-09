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

`direction`是事先写入配置的研究假设，不是看完整样本结果后翻转符号。每个因子先按日
去极值、标准化，再乘方向并等权平均。免费数据没有严格时点市值和历史行业，所以当前
默认关闭市值与行业中性化。

## 每次运行产生什么

- `factor_summary.csv`：每个因子的RankIC、ICIR、胜率和五分组多空差。
- `factor_correlation.csv`：因子之间的平均日度截面相关性，用于识别重复因子。
- `factor_scores.parquet`：完整日频因子矩阵和综合分。
- `latest_signal.csv`：最新交易日全部候选股票及综合排名。
- `target_weights.csv`：仅在配置的调仓日形成的Top 20缓冲目标。

只修改因子或组合配置、无需重新联网下载时运行：

```powershell
.\.venv\Scripts\python.exe scripts\run_factor_suite.py
```

## 如何加入自己的因子

1. 在`src/quant_lab/factors/library.py`写函数，必须先按`symbol`分组再rolling/shift。
2. 加到`FACTOR_REGISTRY`。
3. 在`configs/research.yaml`的`definitions`加入名称和事先约定的方向。
4. 添加测试，至少检查股票之间不串值、窗口缺失期和输入排序。
5. 运行`daily_update.ps1`，同时检查因子IC、分层、换手和相关性，不只看回测收益。

【面试高频】不能在全样本看到IC为负后直接把因子乘以-1，再声称样本外有效。因子方向
必须来自经济假设或只在训练窗口确定，然后在后续时间窗口冻结验证。
