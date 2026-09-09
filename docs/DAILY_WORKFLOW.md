# 免费真实数据：每日收盘后工作流

## 每天运行

建议在收盘且BaoStock当日日线发布后执行：

```powershell
.\scripts\daily_update.ps1
```

系统自动完成：

```text
补充后复权价格（因子）与不复权价格（成交）
→ 更新中证500成分快照和交易日历
→ 完整性、新鲜度、状态与价格审计
→ 11个普通价量因子逐个评价
→ 因子相关性与等权综合分
→ Top 20 / Rank 30缓冲目标持仓
→ 日频数据回测
→ 最新排名与下一交易日纸面操作清单
```

每天优先看：

1. `reports/generated/daily_factor_lab/run_status.json`
2. `reports/generated/daily_factor_lab/latest_signal.csv`
3. `reports/generated/daily_factor_lab/next_day_orders.csv`
4. `reports/generated/daily_factor_lab/REPORT.md`
5. `data/processed/baostock_daily_audit/audit.md`

`latest_signal.csv`每天都会更新。默认`W-FRI`周频调仓，因此普通工作日的
`next_day_orders.csv`会明确写`NO_TRADE`。改成日频后，排名缓冲仍可能让当天没有订单。

## 日频与周频切换

只修改`configs/research.yaml`：

```yaml
portfolio:
  rebalance_frequency: W-FRI  # 默认周频
  top_n: 20
  exit_rank: 30
```

日频改为：

```yaml
rebalance_frequency: D
```

不要修改Python里的数字。配置文件是唯一真源，运行时副本会保存为
`effective_config.yaml`，报告中的Top-N必须与它一致。

## 两套价格

- 后复权`adj_open/adj_close`：因子、标签和收益研究。
- 不复权`open/close`：第二天参考价、整手股数、佣金和现金账本。

第一次升级会为最近800个自然日补一套不复权数据，不下载全部历史。之后两套价格都只
请求本地最后日期后的缺口。

## 纸面账户

首次运行会建立：

```text
data/state/paper_portfolio.json
```

它不会被程序擅自改仓。手动成交后，用实际剩余现金和持仓覆盖它，例如：

```powershell
.\.venv\Scripts\python.exe scripts\set_paper_account.py `
  --cash 350000 `
  --as-of 2026-09-09 `
  --position 600000.SH=1000 `
  --position 000001.SZ=2000
```

下一次运行会根据这份真实纸面持仓计算买卖差额。没有写出的股票视为零持仓，因此每次
要把所有持仓都列全。

## 安全边界

- `REVIEW_REQUIRED`不是自动下单；开盘前必须重新检查停牌、涨跌停、公告和实际余额。
- `NO_TRADE`表示今天计算了信号，但不满足调仓条件或目标没有变化。
- `CALENDAR_UNKNOWN`、审计失败、数据过期时不要交易。
- A股当日买入通常不能当日卖出，本项目按下一交易日开盘执行。
- 首次保存指数快照之前的窗口仍是当前成员回填，不能宣称消除了幸存者偏差。
