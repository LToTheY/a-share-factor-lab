# A-Share Factor Lab

[![CI](https://github.com/LToTheY/a-share-factor-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/LToTheY/a-share-factor-lab/actions/workflows/ci.yml)

面向量化研究实习的A股日频因子研究项目。日行情使用国泰安优先、BaoStock补缺，
每次今日检查联网更新缺失日期并重查最新一天。普通价量因子是当前研究主线；Ridge、LightGBM和可选MLP
滚动样本外框架已经实现，用作对照研究，不直接替代每日纸面信号。
它不是自动实盘交易系统，
而是一条透明、可测试、可复现的研究链路：

```text
CSMAR原始价 + BaoStock补缺 → 统一复权与数据审计 → 时点股票池 → 多个普通因子
→ IC/分层/相关性 → 综合分 → Top 20缓冲目标 → 回测 → 次日纸面清单
```

## 现在从这里开始

网页按数据检查、因子研究、策略实验、实验历史、教程组织。支持自定义因子一键后台研究，
历史沪深300/中证500/并集，5,000元起始实验（可改），当前策略的样本外验证和全部单因素对照。

- [项目使用教程](docs/USER_GUIDE.md)：安装、无账号合成演示、更新、运行、重开、比较与导出。
- [45分钟小白课程](docs/BEGINNER_COURSE.md)：从自定义价量因子到完整实验。
- [成交假设与限制](docs/EXECUTION_ASSUMPTIONS.md)：小资金、板块最低申报量和公司行动近似。
- [因子与策略研究方法](docs/FACTOR_RESEARCH_METHOD.md)：研究模块、次日开盘标签、IC不确定性和预算筛选。
- [自己的策略每日复核](docs/MANUAL_REVIEW.md)：复用已保存策略，填写本机账户快照，联网更新后导出人工清单。
- [项目状态](docs/PROJECT_STATUS.md)与[技术讲述自测](docs/INTERVIEW_WALKTHROUGH.md)：已验收能力、保留限制和能否独立解释核心模块。


**当前数据怎么用：[docs/DATA_USAGE.md](docs/DATA_USAGE.md)**。包含数据位置、来源优先级、
股票池、日常更新、完整回测重建、异常处理和旧数据恢复方法。

1. 研究口径：[docs/RESEARCH_SPEC.md](docs/RESEARCH_SPEC.md)
2. 从零逐课学习因子：[docs/factor_course/README.md](docs/factor_course/README.md)
3. 因子研究完整参考：[docs/FACTOR_RESEARCH_TUTORIAL.md](docs/FACTOR_RESEARCH_TUTORIAL.md)
4. 一边运行一边学习：[docs/LEARNING_GUIDE.md](docs/LEARNING_GUIDE.md)
5. 免费真实数据每日运行：[docs/DAILY_WORKFLOW.md](docs/DAILY_WORKFLOW.md)
6. 普通因子速查：[docs/FACTOR_GUIDE.md](docs/FACTOR_GUIDE.md)
7. 机器学习研究：[docs/ML_RESEARCH.md](docs/ML_RESEARCH.md)
8. 公开研究案例：[docs/CASE_STUDY.md](docs/CASE_STUDY.md)
9. 架构图：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
10. 本地网页看板：[docs/DASHBOARD.md](docs/DASHBOARD.md)
11. 网页策略实验室：[docs/STRATEGY_LAB.md](docs/STRATEGY_LAB.md)
12. CSMAR接入准备与共享股票池：[docs/DATA_MIGRATION.md](docs/DATA_MIGRATION.md)

CSMAR原始下载已完成：508个分区，Parquet约388 MB，实际日行情和财务报告期覆盖
至2024年底；BaoStock已补齐近期行情和两条指数日线。正式价量研究库共3,944,905行、
1,579只历史股票，截至2026-09-29，约267 MB。[下载核验记录](docs/CSMAR_DOWNLOAD_AUDIT.md)。
批量下载入口（当前已完成，无需重复运行）：
`.\.venv\Scripts\python.exe scripts\download_csmar_bulk.py --end 2026-09-28`
并完成一次WRDS/Duo登录，即按分区批量下载2015年以来沪深300＋中证500历史股票池
的日行情、复权及必要财务数据；不再要求先跑小样本。密码不保存，中断可重跑续传。
研究、回测和策略实验室已改用 `data/processed/market_daily.parquet`；旧行情清理及恢复
记录见[数据说明](docs/DATA_USAGE.md)。财报、市值及历史行业仍待口径验收。
其他账号首次使用需先运行 `scripts/probe_wrds_csmar.py`
获取自己的字段目录。详见[接入记录](docs/DATA_MIGRATION.md)。

默认的真实数据更新、最新因子排名和纸面调仓检查：

```powershell
.\scripts\daily_update.ps1
```

合成数据用于无账号演示与自动测试，不作为真实策略业绩。

本地因子研究工作区：

```powershell
.\scripts\run_dashboard.ps1
```

已有虚拟环境的 Windows 用户也可双击项目根目录 `start_dashboard.cmd`。
它只启动本机网页；首次安装、数据更新和研究由教程与网页入口分别处理。

看板提供日频、日内和其他频率的因子档案入口，支持本地保存、编辑、搜索及导出。
日频已有因子诊断；日内和其他频率当前支持建档，尚未接入计算与回测。
看板同时展示数据状态、策略实验、回测和Walk-forward样本外结果，不会修改模拟账户或发送交易指令。

## 已实现

- BaoStock免费真实日线自2015年起的一次性建库和逐日增量更新。
- 历史时点中证500成分快照，避免把今天的成分股机械回填到过去。
- 不复权成交价格与后复权研究价格双轨存储。
- 默认11个价量因子的批量评价、相关性和综合分；另有下行波动、隔夜反转与盘内动量候选。
- 因子等权、模块等权或自定义权重；预算筛选可负担候选，展示实际成交与现金闲置。
- 配置驱动的日频/周频切换、Top 20与Rank 30换手缓冲。
- 最新因子排名、模拟账户和下一交易日人工复核清单。
- Tushare保留为可选付费/交叉验证数据源。
- 确定性合成数据仅作为测试夹具。
- 历史上市天数、ST、停牌和最低成交额股票池过滤接口。
- 动量、短期反转、Amihud、波动率、特质波动率、BP、EP因子。
- MAD去极值、截面标准化、行业/市值中性化。
- 收盘及次日开盘标签、RankIC、ICIR、HAC不确定性、年度表现与含标签覆盖率的分层收益。
- 周频Top-N目标权重。
- 下一交易日开盘执行、现金/持仓账本、滑点、佣金、印花税、最低佣金、
  整数手、停牌及涨跌停限制。
- 年化收益、波动、Sharpe、最大回撤、Calmar和换手率。
- 训练集拟合缺失值与标准化参数的NumPy Ridge基线。
- 可选LightGBM和PyTorch MLP适配器。
- Qlib Alpha158 + LightGBM示例配置。
- pytest回归测试、网页完整流程验收和Windows/Linux GitHub Actions持续集成。

## 重要边界

当前版本是可运行的研究MVP，不应直接用于实盘：

- 免费入口用BaoStock提供的历史ST/交易状态，但没有严格历史行业和时点市值，默认不做
  行业/市值中性化。
- 免费接口的指数成分是周期性快照而非每个交易日逐笔变更记录，调仓生效日附近仍可能有
  少量时点误差。
- 回测未实现历史申万行业、严格时点市值、公司行动现金流、成交量容量模型、
  风险模型和实盘订单管理。
- 合成数据结果只证明代码能运行，不能写成策略业绩。
- Qlib配置需要依据安装版本核对，交易成本也必须更新为研究时点的真实假设。

这些限制必须留在报告里。面试中主动说明限制，通常比隐藏限制更加分。

## 1. 安装

建议使用Python 3.10～3.12。Windows PowerShell：

```powershell
cd "<项目目录>"
.\scripts\bootstrap.ps1
```

如果PowerShell禁止激活脚本，可以不激活，直接使用：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[free-data,dashboard,dev]"
.\.venv\Scripts\python.exe scripts\run_tests.py
```

只运行合成数据和Ridge最小流程：

```powershell
python -m pip install -e .
python scripts\run_tests.py
```

日频价量网页不需要安装PyTorch、LightGBM或WRDS。确认要使用相关模块后再按其教程安装
对应可选依赖；`all`包含深度学习等较大的包，不是小存储设备的默认安装选项。

## 2. 运行免费真实数据每日流程

```powershell
.\scripts\daily_update.ps1
```

每日入口现在先联网更新交易日历、当前股票池和日行情，严格检查最新交易日及因子
观察窗口，再计算最新排名与纸面调仓建议。也可在看板的“今日调仓检查”页面点击
“更新数据并检查调仓”。默认北京时间18:00以后要求当天日行情，此前使用上一交易日。
更新失败或覆盖不足时明确阻止建议，并隐藏过期结果。

当前检查状态：`data/state/current_check.json`；本次排名和建议位于
`reports/generated/current_check/runs/<run_id>/`。既有CSMAR原始值优先，BaoStock补缺，
复权统一生成，不直接拼接两家的复权价格。模拟账户只读取，不会自动下单或改仓。

长历史下载、财报时点整理和完整回测是独立研究任务。已有完整历史报告仍保留在
`reports/generated/daily_factor_lab/`，以下文件不代表每次日常检查都会重新生成：

- `result_manifest.json`（历史研究）或 `data/state/current_check.json`（最新检查）：本次数据日期、审计和订单状态；
- `latest_signal.csv`：最新截面的11因子综合排名；
- `next_day_orders.csv`：下一交易日纸面复核清单，也可能明确写`NO_TRADE`；
- `factor_summary.csv`和`factor_correlation.csv`：单因子评价和相关性；
- `factor_coverage.csv`和`factor_stability.csv`：每日覆盖率、年度及近期稳定性；
- `benchmark_equity.csv`：历史时点成分股等权、每日再平衡、无成本的诊断基准；
- `walk_forward/`：5年训练、1年验证、1年样本外测试的滚动结果；
- `target_weights.csv`：统一Top 20、Rank 30退出缓冲后的目标持仓；
- `equity.csv`和`trades.csv`：净值及逐笔交易；
- `REPORT.md`：可阅读的研究结论和限制。

完整说明见[每日增量工作流](docs/DAILY_WORKFLOW.md)。

## 3. 可选：用Tushare做第二数据源核验

复制环境变量示例：

```powershell
Copy-Item .env.example .env
```

在`.env`填入Token。该文件已被Git忽略。然后：

```powershell
python scripts\download_tushare.py --start 20180101 --end 20251231
python scripts\build_dataset.py
```

这不是默认流程，仅在以后有Token并希望交叉核验数据时使用。下载采用“一日一个
Parquet分区”，中断后重跑会跳过已有文件。先测试一个月：

```powershell
python scripts\download_tushare.py --start 20250101 --end 20250131
```

检查单位和随机样本后，再扩大区间。

## 4. 单独重跑已有真实数据的多因子研究

```powershell
.\.venv\Scripts\python.exe scripts\run_factor_suite.py
```

这条命令不会联网，只用已经缓存的`data/processed/market_daily.parquet`。日常使用仍应
运行`daily_update.ps1`，因为它会先补新数据再检查当前调仓。历史报告重跑不代表数据已更新。

主线的11个因子及方向统一写在`configs/research.yaml`，不在脚本中另外维护。
`run_momentum.ps1`和`run_momentum_study.py`仅保留为早期单因子实验兼容入口，
不再作为每日主流程或简历结果入口。

## 5. 研究时点约定

本项目默认：

1. 第`t`日收盘后计算因子。
2. 目标权重记录在第`t`日。
3. 回测严格在下一交易日开盘执行。
4. 因子同时报告`t`收盘至`t+N`收盘、`t+1`开盘至`t+N+1`开盘两种标签，只用于研究评价和训练。
5. 涨停禁止买，跌停禁止卖，停牌双向禁止；失败持仓继续保留。

如果你改成开盘信号、VWAP或收盘成交，必须同步修改标签、数据可见时间和测试。

## 6. 因子开发规范

新因子应加入`src/quant_lab/factors/library.py`：

```python
def my_factor(frame: pd.DataFrame) -> pd.Series:
    # rolling/shift必须先按symbol分组，防止股票之间串数据。
    # 因子收益使用后复权价；原始close只用于模拟成交、股数与费用。
    returns = frame.groupby("symbol", sort=False)["adj_close"].pct_change(fill_method=None)
    return (
        returns.groupby(frame["symbol"], sort=False)
        .rolling(20, min_periods=15)
        .mean()
        .reset_index(level=0, drop=True)
    )


FACTOR_REGISTRY["my_factor"] = my_factor
```

同时必须添加测试，至少验证：

- 两只数量级完全不同的股票不会互相污染；
- 前`window-1`天是缺失值；
- 打乱输入后排序仍产生相同结果；
- 复权因子变化不会产生虚假收益。

## 7. 机器学习

真实数据滚动研究入口：

```powershell
.\.venv\Scripts\python.exe scripts\run_ml_study.py
.\.venv\Scripts\python.exe scripts\run_ml_study.py --models ridge lightgbm
```

流程使用5年训练、1年验证、1年测试和5交易日隔离带，输出逐折模型、样本外预测、
RankIC、含成本/无成本组合和预设组合敏感性。完整口径见
[机器学习研究主线](docs/ML_RESEARCH.md)。`pipeline.py`仍保留为合成数据教学入口。

模型顺序建议固定为：

```text
因子等权 → Ridge → LightGBM → MLP → 有明确时序结构后再做LSTM/Transformer
```

不要只报告MSE或方向准确率；至少报告每日RankIC、ICIR、Top-K组合、换手率和
加成本后的样本外表现。

## 8. Qlib

建议在WSL2 Ubuntu中单独建立Qlib环境。准备中国市场数据后运行：

```bash
qrun configs/qlib_alpha158_lightgbm.yaml
```

先把它当作独立基准，不要让Qlib替换本项目的数据时点检查。跑通后比较：

- 相同股票池和时间区间；
- 相同标签；
- 相同交易成本；
- Alpha158、你的四个因子及二者合并；
- Ridge、LightGBM和MLP；
- 本地回测与Qlib/聚宽回测差异。

## 9. 测试

```powershell
python scripts\run_tests.py
```

安装开发依赖后也可以：

```powershell
python -m pytest --basetemp data/interim/pytest-local
python -m ruff check .
```

任何影响信号时点、成交、费用或数据分组的修改都必须先加测试。

## 10. 项目导航

```text
configs/                 BaoStock、研究、可选Tushare和Qlib配置
data/                    raw/interim/processed，数据本身不提交Git
docs/DATA_CONTRACT.md    字段、单位和时点要求
docs/RESEARCH_CHECKLIST.md 完成报告前逐项检查
reports/                 报告模板和生成结果
scripts/                 下载、合并、研究、演示和测试入口
src/quant_lab/data       数据契约、审计、BaoStock增量更新、可选Tushare
src/quant_lab/universe   历史股票池
src/quant_lab/factors    因子库
src/quant_lab/evaluation 预处理、IC和分层
src/quant_lab/portfolio  目标权重
src/quant_lab/backtest   执行账本和绩效
src/quant_lab/models     Ridge、LightGBM和MLP
tests/                   防未来函数和回归测试
```

## 11. 简历交付标准

完成一个因子前，逐项执行`docs/RESEARCH_CHECKLIST.md`，并用
`reports/factor_report_template.md`写报告。Git仓库只提交：

- 源代码、配置和测试；
- 少量经过许可的演示样本或合成数据；
- README、环境说明和研究报告；
- 不提交Token、收费数据或无再分发权的数据。

只有真实数据样本外结果才可以进入简历，所有数字必须能从固定配置重新生成。

## 免责声明

本项目仅用于学习和研究，不构成投资建议。数据许可、复权、时点和交易规则应由
研究者自行核验。
