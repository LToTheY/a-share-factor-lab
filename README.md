# A-Share Factor Lab

[![CI](https://github.com/LToTheY/a-share-factor-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/LToTheY/a-share-factor-lab/actions/workflows/ci.yml)

面向量化研究实习的A股日频因子研究项目。默认使用免费的BaoStock真实日线，
每次启动只补充缺失日期。普通价量因子是当前研究主线；Ridge、LightGBM和可选MLP
滚动样本外框架已经实现，用作对照研究，不直接替代每日纸面信号。
它不是自动实盘交易系统，
而是一条透明、可测试、可复现的研究链路：

```text
原始价+后复权价增量下载 → 数据审计 → 时点股票池 → 多个普通因子
→ IC/分层/相关性 → 综合分 → Top 20缓冲目标 → 回测 → 次日纸面清单
```

## 现在从这里开始

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

默认的真实数据更新、多因子研究和纸面清单：

```powershell
.\scripts\daily_update.ps1
```

合成数据仍只用于自动测试，不作为默认研究数据或简历结果。

本地只读研究看板：

```powershell
.\scripts\run_dashboard.ps1
```

看板展示数据状态、因子诊断、回测和Walk-forward样本外结果，不会修改模拟账户或发送交易指令。

## 已实现

- BaoStock免费真实日线自2015年起的一次性建库和逐日增量更新。
- 历史时点中证500成分快照，避免把今天的成分股机械回填到过去。
- 不复权成交价格与后复权研究价格双轨存储。
- 11个普通价量因子的批量评价、相关性和等权综合分。
- 配置驱动的日频/周频切换、Top 20与Rank 30换手缓冲。
- 最新因子排名、模拟账户和下一交易日人工复核清单。
- Tushare保留为可选付费/交叉验证数据源。
- 确定性合成数据仅作为测试夹具。
- 历史上市天数、ST、停牌和最低成交额股票池过滤接口。
- 动量、短期反转、Amihud、波动率、特质波动率、BP、EP因子。
- MAD去极值、截面标准化、行业/市值中性化。
- 未来收益、RankIC、ICIR、胜率和分层收益。
- 周频Top-N目标权重。
- 下一交易日开盘执行、现金/持仓账本、滑点、佣金、印花税、最低佣金、
  整数手、停牌及涨跌停限制。
- 年化收益、波动、Sharpe、最大回撤、Calmar和换手率。
- 训练集拟合缺失值与标准化参数的NumPy Ridge基线。
- 可选LightGBM和PyTorch MLP适配器。
- Qlib Alpha158 + LightGBM示例配置。
- 标准库测试、端到端烟雾测试和GitHub Actions持续集成。

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
cd "C:\Users\lenovo\Desktop\水滴石穿\a-share-factor-lab"
.\scripts\bootstrap.ps1
```

如果PowerShell禁止激活脚本，可以不激活，直接使用：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[all]"
.\.venv\Scripts\python.exe scripts\run_tests.py
```

只运行合成数据和Ridge最小流程：

```powershell
python -m pip install -e .
python scripts\run_tests.py
```

## 2. 运行免费真实数据每日流程

```powershell
.\scripts\daily_update.ps1
```

第一次会建立自2015-01-01起的长历史库（2015年主要作为因子预热期，正式研究从
2016-01-01开始）；以后每次只向BaoStock请求本地缺失的日期。输出在
`reports/generated/daily_factor_lab/`：

- `run_status.json`：本次数据日期、审计和订单状态；
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

这条命令不会联网，只用已经缓存的`data/processed/baostock_daily.parquet`。日常使用仍应
运行`daily_update.ps1`，因为它会先补新数据再研究。

主线的11个因子及方向统一写在`configs/research.yaml`，不在脚本中另外维护。
`run_momentum.ps1`和`run_momentum_study.py`仅保留为早期单因子实验兼容入口，
不再作为每日主流程或简历结果入口。

## 5. 研究时点约定

本项目默认：

1. 第`t`日收盘后计算因子。
2. 目标权重记录在第`t`日。
3. 回测严格在下一交易日开盘执行。
4. 因子标签是`t`收盘至`t+N`收盘收益，只用于研究评价和训练。
5. 涨停禁止买，跌停禁止卖，停牌双向禁止；失败持仓继续保留。

如果你改成开盘信号、VWAP或收盘成交，必须同步修改标签、数据可见时间和测试。

## 6. 因子开发规范

新因子应加入`src/quant_lab/factors/library.py`：

```python
def my_factor(frame: pd.DataFrame) -> pd.Series:
    # rolling/shift必须先按symbol分组，防止股票之间串数据。
    # 因子收益使用后复权价；原始close只用于模拟成交、股数与费用。
    returns = frame.groupby("symbol", sort=False)["adj_close"].pct_change()
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
pytest
ruff check .
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
