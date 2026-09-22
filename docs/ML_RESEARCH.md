# 机器学习研究主线

## 目标与边界

这条流程回答一个具体问题：在完全相同的历史股票池、11个普通因子、未来5日标签和
交易规则下，训练窗口拟合的模型是否稳定优于事先固定方向的等权综合分。

它不是为了证明“机器学习一定更好”。模型顺序固定为：

```text
等权基线 → Ridge → LightGBM → 可选MLP → 有明确时序增益后才考虑序列模型
```

默认不进入每日信号流程，也不修改纸面账户。只有模型连续多折改善样本外组合、成本后
收益和稳定性，才讨论替换当前综合分。

## 运行

先完成真实数据和普通因子更新：

```powershell
.\scripts\daily_update.ps1
```

默认只跑等权与Ridge：

```powershell
.\.venv\Scripts\python.exe scripts\run_ml_study.py
```

运行已经安装的LightGBM基准：

```powershell
.\.venv\Scripts\python.exe scripts\run_ml_study.py --models ridge lightgbm
```

MLP使用可选PyTorch依赖。当前开发环境已安装`torch 2.6.0+cu124`并验证RTX 3060
可用；新环境可先安装项目的深度学习依赖，再运行三模型比较：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dl]"
.\.venv\Scripts\python.exe scripts\run_ml_study.py --models ridge lightgbm mlp
```

配置唯一入口是`configs/ml_research.yaml`。命令行`--models`只覆盖本次模型列表，实际
使用的列表和输入文件元数据保存在`run_manifest.json`。

## 防泄漏约定

- 每折使用5年训练、1年验证、1年测试，按自然年向前滚动。
- 训练与验证窗口末尾均删除5个交易日，避免未来5日标签跨过边界。
- Ridge只用训练/拟合样本计算缺失值中位数、均值和标准差。
- Ridge的alpha只看验证集每日RankIC；测试期不参与选择。
- LightGBM只用验证集早停。当前接口使用官方回调，并兼容新版`eval_X/eval_y`。
- MLP保存验证损失最佳权重，而不是最后一轮权重；模型和DataLoader随机源固定。
- 每折模型单独保存，不把后续年份重新训练的参数用于过去预测。

LightGBM早停接口参考[官方LGBMRegressor文档](https://lightgbm.readthedocs.io/en/latest/pythonapi/lightgbm.LGBMRegressor.html)。
PyTorch随机性边界参考[官方随机数文档](https://docs.pytorch.org/docs/stable/random.html)和
[DataLoader文档](https://docs.pytorch.org/docs/main/data.html)。固定种子不能保证跨版本、
跨平台得到逐位相同结果，因此`summary.json`同时记录环境版本。

## 输出

默认目录为`reports/generated/ml_factor_lab/`：

- `REPORT.md`、`summary.json`：最终比较和机器可读摘要；
- `run_manifest.json`、`effective_ml_config.yaml`：模型列表、配置哈希和输入元数据；
- `oos_predictions.parquet`：所有模型的逐股滚动样本外预测；
- `fold_summary.csv`：逐折验证/测试IC、Ridge alpha和LightGBM最佳轮数；
- `feature_importance.csv`、`feature_stability.csv`：逐折及跨折特征方向稳定性；
- `portfolio_sensitivity.csv`：预先配置的缓冲、持仓数和调仓频率敏感性；
- `fold_models/`：每折Ridge参数、LightGBM Booster及可选MLP检查点；
- `<model>/`：IC、目标权重、含成本/无成本净值和逐笔交易。

## 2026-09-17真实结果

样本外区间为2022-01-04至2026-09-16：

| 模型 | 平均RankIC | ICIR | 周频含成本年化 | 周频无成本年化 | 年化成本拖累 |
|---|---:|---:|---:|---:|---:|
| 等权 | 0.0273 | 1.05 | -6.60% | 0.86% | 7.46个百分点 |
| Ridge | 0.0661 | 2.12 | -1.94% | 3.32% | 5.26个百分点 |
| LightGBM | 0.0613 | 2.23 | -2.05% | 4.16% | 6.21个百分点 |

同期无成本诊断基准年化约3.66%。Ridge显著改善排序，LightGBM的非线性没有形成稳定
净增益；两者周频成本后都落后基准。预设敏感性中，月频Ridge含成本年化约1.84%，说明
降低换手有帮助，但该结果仍低于基准，而且5日标签与月频持有期并不完全匹配，不能据此
直接改主策略。

当前结论是保留Ridge为主要ML基线、LightGBM为非线性对照。MLP运行环境和代码测试
已经就绪，但尚未形成完整真实数据滚动结果；序列模型尚未实现。下一项研究应先直接
约束换手或使用与持有期一致的标签，而不是仅靠增加模型复杂度。
