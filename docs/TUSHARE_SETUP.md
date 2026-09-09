# Tushare真实数据启用

## 1. 准备Token

复制`.env.example`为`.env`，只在本机填写：

```env
TUSHARE_TOKEN=你的Token
```

`.env`已在`.gitignore`中。不要把Token粘贴到报告、Notebook或Git提交。

## 2. 权限预检

阶段3完整口径依赖：

| 数据 | 接口 | 用途 | 官方说明 |
|---|---|---|---|
| 日线 | `daily` | OHLCV | 账户权限决定历史和频率 |
| 复权因子 | `adj_factor` | 连续收益 | 统一复权口径 |
| 每日指标 | `daily_basic` | 市值、换手 | 通常至少2000积分 |
| 涨跌停价格 | `stk_limit` | 下一开盘成交限制 | 通常至少2000积分 |
| 历史ST列表 | `stock_st` | 避免当前名称反推历史 | 官方说明3000积分起 |
| 停复牌 | `suspend_d` | 明确不可交易日 | 每日停复牌记录 |
| 指数权重 | `index_weight` | 中证500历史成分 | 通常至少2000积分，月度获取 |

官方文档：

- <https://tushare.pro/document/2?doc_id=32>
- <https://tushare.pro/document/2?doc_id=183>
- <https://tushare.pro/document/2?doc_id=397>
- <https://tushare.pro/document/2?doc_id=214>
- <https://tushare.pro/document/2?doc_id=96>

接口权限可能变化，应以实际账户返回和官方文档为准。

## 3. 小区间试下载

先下载一个月，避免在字段或权限错误时浪费调用额度：

```powershell
.\.venv\Scripts\python.exe scripts\download_tushare.py `
  --start 20250101 `
  --end 20250131 `
  --index-code 000905.SH
```

检查：

- `data/raw/tushare/download_manifest.json`
- 是否有`warnings`
- 日分区是否完整
- `trade_calendar.parquet`
- `index_000905_SH.parquet`

## 4. 合并和审计

```powershell
.\.venv\Scripts\python.exe scripts\build_dataset.py
.\.venv\Scripts\python.exe scripts\audit_data.py
```

如果审计失败，先看：

```text
data/processed/audit/audit.md
```

不要使用`--allow-audit-errors`生成简历结果。该开关只用于定位字段问题。

## 5. 扩展到正式区间

小样本通过后，再下载2015年至最近完整自然年末。下载器按交易日保存，重跑会跳过
已有分区。正式运行前随机抽样至少20只股票，与聚宽或另一可靠数据源核对：

- 未复权收盘价
- 复权方向
- 成交量/成交额单位
- 市值单位
- ST、停牌和涨跌停状态
- 指数成分生效时间

