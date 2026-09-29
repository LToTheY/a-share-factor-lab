# CSMAR优先、BaoStock补缺：每日调仓检查

更新于2026-09-30。个人小资金复核请优先进入“我的策略每日复核”，选择已保存的真实
数据实验并填写自己的现金与完整持仓，详见 [个人策略复核教程](MANUAL_REVIEW.md)。
它使用独立账户快照，不覆盖项目原有模拟账户。

下文的“今日调仓检查”和命令行入口用于项目默认配置与模拟账户。日常检查与完整历史
研究分开运行；未连接券商，不会发送真实交易指令。

## 每天运行

建议在收盘且BaoStock当日日线发布后执行：

```powershell
.\scripts\daily_update.ps1
```

若项目从其他目录迁移过，先运行 `scripts\doctor.py`。其中
`editable_install_matches_project` 必须为 `true`；否则执行
`.\.venv\Scripts\python.exe -m pip install --no-deps -e .`，避免 Python 继续导入旧目录。

系统自动完成：

```text
北京时间和交易日历确定最新已完成交易日
→ 联网确认沪深300、中证500当前成分及上市日期
→ 复用本地CSMAR/BaoStock，增量补日期并重新核对最新一天
→ 同步补沪深300、中证500指数日线，避免股票与对照指数日期错位
→ 验证当前研究池及已有持仓的最新截面、121交易日因子窗口
→ 明确停牌记录补零成交量额，其他缺失拒绝发布建议
→ 不复权价配合交易所昨收，统一计算连续区间复权价
→ 计算最新11个因子、覆盖率与等权综合分
→ 根据模拟账户实际持仓形成Top 20 / Rank 30缓冲目标
→ 输出无需调仓（NO_TRADE）或需人工复核（REVIEW_REQUIRED）
```

每天优先看：

1. `data/state/current_check.json`：本次状态、目标交易日、检查结果、配置指纹及报告路径。
2. `reports/generated/current_check/runs/<run_id>/latest_signal.csv`：本次最新排名。
3. `reports/generated/current_check/runs/<run_id>/next_day_orders.csv`：本次纸面建议。
4. `reports/generated/current_check/next_day_orders.csv`：便于查看的当前副本；失败时写入
   `DATA_NOT_READY`，不会让旧买卖清单继续充当本轮结果。
5. `data/processed/current_market.parquet`：通过检查的近期共享行情窗口。

看板每次打开都会根据交易日历重新检查结果是否过期，配置或模拟持仓变动也会要求
重算。默认18:00以后要求当天日线；盘中使用上一交易日，不把未收盘行情当日线。
该更新时间是保守运行约定，并非保证供应商18:00已发布：未发布时仍会阻止建议。

完整历史报告与Walk-forward继续保留在 `reports/generated/daily_factor_lab/`。
日常检查不会拿当前成分股倒推历史业绩，也不会每天重复十年回测。

历史研究正式读取 `data/processed/market_daily.parquet`，旧行情入口已停用。
需要同步完整历史报告时运行 `.\scripts\daily_update.ps1 --full-research`；只重算本地
因子时运行 `scripts/run_factor_suite.py`。数据目录、来源、质量标记和恢复记录见
[当前数据使用说明](DATA_USAGE.md)。

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

不要修改Python里的数字。配置文件是唯一真源，当前检查保存配置SHA256；运行中
配置或持仓改变会拒绝发布结果。完整历史报告另保存 `effective_config.yaml`。

## 两套价格

- `adj_open/adj_close`：以原始价与交易所昨收统一构造，因子只跨连续观测区间计算。
- 不复权`open/close`：第二天参考价、整手股数、佣金和现金账本。

每次复用本地历史，仅补当前池和持仓所需窗口及缺口，且重新查询最新交易日，避免
先前的部分截面缓存被误认为完整。原始值逐字段优先CSMAR、其次BaoStock，保留来源；
财报、市值与行业尚未验收的字段不会自动加入当前价量模型。

调整方法依据[BaoStock复权说明](https://www.baostock.com/helpdocs/pdf/BaoStock%E5%A4%8D%E6%9D%83%E5%9B%A0%E5%AD%90%E7%AE%80%E4%BB%8B.pdf)，
是价格收益研究近似，不等于现金分红再投资账本。
涨跌停规则按日期处理：创业板/科创板的20%不被ST的旧5%分支覆盖；沪深主板ST
自2026-07-06改为10%，以[上交所正式发布](https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20260424_10816474.shtml)
和[深交所交易规则](https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf)
为依据。特殊复牌等无法确认的情形标记未知并阻止本次建议。

## 纸面账户

当前检查读取以下模拟账户，不自动创建或覆盖持仓：

```text
data/state/paper_portfolio.json
```

它不会被程序擅自改仓。以下是维护这一模拟账户的高级命令；个人策略请在“我的策略
每日复核”重新创建现金和持仓快照。确需维护默认模拟账户时，使用完整的剩余现金与持仓，例如：

```powershell
.\.venv\Scripts\python.exe scripts\set_paper_account.py `
  --cash 350000 `
  --as-of 2026-09-09 `
  --position 600000.SH=1000 `
  --position 000001.SZ=2000
```

下一次运行会根据这份模拟账户持仓计算买卖差额。没有写出的股票视为零持仓，因此每次
要把所有持仓都列全。

若没有该文件，当前检查只以研究配置的初始资金和空仓进行纸面演示，并在界面明确
标注模拟账户。不能把项目默认的100万元研究资金当作实际可投入金额；个人资金、
实验中的初始资金和实际现金余额也需要分别理解。

## 安全边界

- `REVIEW_REQUIRED`不是自动下单；开盘前必须重新检查停牌、涨跌停、公告和实际余额。
- `NO_TRADE`表示今天计算了信号，但不满足调仓条件或目标没有变化。
- `CALENDAR_UNKNOWN`、`PRICE_MISSING`、审计失败、数据过期时不要交易。
- 纸面账户中的已有持仓即使跌出当前股票池，也会使用最新全市场截面价格计入账户净值；
  若任何已有持仓缺少有效价格，系统停止生成调仓建议，要求先人工核对账户状态。
- A股当日买入通常不能当日卖出，本项目按下一交易日开盘执行。
- 免费接口的历史成分按周采样并在周内沿用最近已知名单；指数调整生效日附近可能存在
  少量时点误差，报告中必须保留这一限制。
