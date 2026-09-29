# 当前数据使用说明

维护日期：2026-09-29（北京时间）。这是当前数据入口、口径和更新方式的主要说明；早期迁移日志中的“等待补齐/尚未切换”只描述当时状态。

## 当前采用什么数据

- 正式价量研究库：`data/processed/market_daily.parquet`。不再使用 `baostock_daily.parquet`。
- 主数据 CSMAR（国泰安），缺失字段/日期用 BaoStock 补充。当前 WRDS 账号下载的 CSMAR 日行情实际截至 2024-12-31，所以 2025 年以来主要来自 BaoStock；不是把近期行情误标为国泰安。
- 日线范围实际为 **2015-01-05—2026-09-29，3,944,905 行、1,579 只历史股票**。研究评价默认从 2016 年开始，2015 年用于预热。未下载分钟/Tick 数据。
- 股票池是沪深300和中证500历史成员的并集，共享一套物理行情。`in_hs300`、`in_zz500`、`in_csi800` 是当时成员标记；`in_index` 当前选择中证500。当前成员分别为300/500只，9月29日历史池中仍有日行情的股票为1,491只。
- 11个价量因子已接入。财报原始数据已保留，但公告日/修订、历史市值单位与行业口径尚未验收，**财务因子和市值/行业中性化尚未启用**。

## 文件在哪里

下列路径均相对 `a-share-factor-lab/`，不要把 `data/` 和账户文件提交到 Git。

| 路径 | 用途 |
| --- | --- |
| `data/raw/csmar/bulk/` | 国泰安原始分区，保留价量及财报等11张表；508分区约388 MB |
| `data/raw/baostock_consolidated/` | 按年压缩的 BaoStock 独立原始库；已核对归档前后数值，删除旧缓存后仍可独立读取 |
| `data/raw/baostock_supplement/raw_daily/` | 每只股票的新增补充；历史缺口和新成员回补 |
| `data/raw/baostock_supplement/cross_sections/` | 最新交易日截面；同日重查的版本优先于旧缓存 |
| `data/raw/baostock_supplement/benchmarks/` | 沪深300、中证500指数日线；具体文件见目录 |
| `data/raw/market_reference/` | 交易日历、历史成分、1,579只股票代码范围 |
| `data/raw/baostock_supplement/reference/` | 最新成分、上市/退市信息、近期交易日历 |
| `data/processed/csmar_staging/raw_daily/` | 按年合并的未复权审计中间表，保留逐字段来源及供应商对照信息 |
| `data/processed/market_daily.parquet` | 正式历史价量研究库，约267 MB |
| `data/processed/market_daily.manifest.json` | 数据版本ID、SHA256、范围、质量及使用限制 |
| `data/processed/current_market.parquet` | 每次今日检查使用的近期260交易日窗口，约12.5 MB |
| `reports/generated/daily_factor_lab/` | 与正式库版本一致的因子分数、回测、Walk-forward报告 |
| `reports/generated/current_check/runs/` | 每次联网检查的排名和纸面建议；以状态文件指向的当次目录为准 |
| `data/state/current_check.json` | 最新检查状态、数据日期、配置及模拟持仓指纹 |

## 价格与质量口径

1. `open/high/low/close/preclose` 是未复权价格（元），`volume` 为股，`amount` 为元。保留各字段的 `*_source`；已观察到的 CSMAR 值优先，缺失才用 BaoStock。
2. `adj_*` 从统一原始价格和交易所昨收链构造；连续区间起点系数为1。因子与未来收益标签使用调整后价格，成交股数与费用使用原始价格。不能直接拼接两家供应商的复权价。
3. 未观察到的交易日或无效复权衔接会开启新 `research_segment`；滚动因子、未来标签、等权诊断基准不会跨这个边界计算。只有明确停牌记录才允许补价格/零成交，不把供应商缺口当停牌。
4. 两条历史成交额异常保留原值：`001872.SZ / 2017-10-24`、`002500.SZ / 2020-06-17`。`amount_outside_price_range=True`，对应成交额因子输入被屏蔽，该行不参加选股及模拟成交，不偷偷改成另一来源的数值。
5. 全库有34行涨跌停状态无法可靠确定，其中6行属于当时中证500成员。保留 `limit_status_known=False`，排除选股和模拟成交。ST/停牌状态未知行数为0。最新窗口另有严格检查。
6. 历史成分用当天及之前最近的供应商快照，不能用今天的500只倒推十年回测。5次请求对应4个生效日只返回499只：2019-01-07、2019-01-14、2021-09-13、2021-09-27，影响22个交易日。**使用已知成员子集**并保留 `membership_complete=False`；不猜补第500只，不能声称完整精确复现官方指数。周度采样还存在调整生效日附近的时点误差。
7. 价格链复权不是现金分红再投资账本；回测尚未逐笔入账分红送转，结果仍是研究近似。指数日线与股票等权诊断基准是不同概念。

## 每次判断是否需要调仓

在网页点“今日调仓检查”，或者在项目终端运行：

```powershell
.\scripts\daily_update.ps1
```

每次先联网核验日历和成分、重查最新一天，并补齐当前池/模拟持仓的观察窗口，再算因子。默认北京时间18:00之后要求当天已完成日线；盘中使用上一交易日。18:00只是本项目运行约定，不保证供应商届时已经发布。

网络失败、行情不全、结果过期、配置或模拟持仓变动，都会阻止旧建议继续显示为本次结果。它不提供盘中实时价，不自动下单。项目里100万元是演示模拟账户，不是用户实际可投资金额。

**每日检查只更新近期窗口和原始补充，不自动重跑十年历史研究。** 看历史报告时应核对报告日期，不能把历史报告的日期当作今日检查日期。

## 重建完整历史研究

需要把新增日期加入完整回测时：

```powershell
.\scripts\daily_update.ps1 --full-research
```

该入口先做今日检查，再补历史并集缺口、重建合并分区与正式库，最后重算因子/回测。可能需数分钟以上；失败会报错，不发布不完整报告。它不会重建已删除的旧行情目录。

只重算现有正式库的因子（不联网）：

```powershell
.\.venv\Scripts\python.exe scripts\run_factor_suite.py
```

只从已核验的本地中间表重建正式库：

```powershell
.\.venv\Scripts\python.exe scripts\build_market_dataset.py
```

改变研究指数时，当前检查由 `configs/research.yaml` 中 `universe.preferred_index` 控制；完整历史库也要用同一指数重建（`--full-research` 会自动传入）。直接调用构建脚本可传 `--index 000300.SH`。两个指数共用原始数据，无需再下载一整份。

`dataset_provenance.json` 将因子分数绑定到行情的 `dataset_id`。策略实验室和 ML 入口拒绝将旧因子分数与新行情混用。新报告完整生成后才替换默认报告，原报告移到 `reports/archive/`；历史研究、案例和已保存实验不会被当成新数据重算结果。ML模型入口已经改用新库，但旧ML业绩需要另行重跑，不能沿用成新数据结论。

## 清理与恢复

授权清理范围是旧 `data/raw/baostock_daily/raw_daily/`、`data/raw/baostock_daily/daily/`、`data/raw/baostock/daily/` 和 `data/processed/baostock_daily.parquet`，共2,753个文件、约1.056 GB。历史成员、日历、模拟账户、实时信号记录和原研究报告保留；未触及桌面“机器学习”旧项目。

- 精确清单：`data/state/legacy_market_cleanup_plan.json`。
- 实际删除结果：`data/state/legacy_market_cleanup_result.json`，以其中 `status=completed` 为完成依据。
- 恢复包位置与校验和：`data/state/legacy_market_recovery.json`，对应 `data/recovery/legacy_market_*.zip`。
- 恢复包包含旧正式行情的原始文件、旧小缓存及清单；归档前后 SHA256 一致。其余旧逐股缓存的标准化原始值已归入独立 BaoStock 年分区，旧调整价可从旧正式行情恢复。
- 需要回滚时，先按恢复记录核对 ZIP 的 SHA256，再解出旧正式行情到独立路径，连同 `reports/archive/` 中匹配的旧报告使用。不要仅恢复旧分数却继续读取新行情。

容量口径、验收结果和切换状态还保存在 `data/state/data_migration_status.json`。正式行情267 MB是单个文件大小，不是整个项目大小；国泰安原始数据、独立 BaoStock 原始库、审计中间表、恢复包和研究报告另占空间。


## 2026-09-30研究入口与容量基线

2026-09-29完成旧行情清理后的测量为：data共1,606,665,384字节（约1.61 GB，包含313 MB恢复包），
reports共659,187,870字节（约0.66 GB），合计约2.27 GB，不包含虚拟环境。
该数字是当时快照，新增实验会增加占用。实际最终测量保存在本机验收记录中。

网页与 `scripts/run_factor_suite.py` 现在共用 `research/service.py`。除默认完整报告外，
网页每次研究单独写入 `reports/generated/jobs/`；失败候选目录不会作为成功结果展示。
研究与策略同时记录数据哈希、因子代码指纹、股票池和完整参数。行情或代码变化时先重跑研究。
选择沪深300或并集不复制原始行情；重新执行对应池的截面标准化。
公开仓库不包含真实原始行情、研究分数、恢复包、账户或衍生交易明细。
