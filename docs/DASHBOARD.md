# 本地因子研究看板

这个看板把 `reports/generated/daily_factor_lab/` 中已有的研究产物展示在网页上。
它是只读研究工具，不训练模型、不修改模拟账户，也不发送真实交易指令。

## 1. 安装

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dashboard]"
```

如果已经安装完整环境 `.[all]`，不需要重复安装。

## 2. 准备研究结果

看板不会代替研究流程。如果报告目录还没有结果，先执行：

```powershell
.\scripts\daily_update.ps1
```

如果只是验证代码，可按项目测试和合成数据说明生成测试结果；不能把合成数据表现解释成真实策略业绩。

## 3. 启动

```powershell
.\scripts\run_dashboard.ps1
```

也可以直接运行：

```powershell
.\.venv\Scripts\python.exe -m streamlit run dashboard\app.py
```

浏览器通常会自动打开 `http://localhost:8501`。停止服务时在运行窗口按 `Ctrl+C`。

## 4. 页面说明

- **系统总览**：先检查数据日期、审计状态、研究边界和最新排名。
- **因子研究**：查看单因子 IC、年度稳定性、截面分布和因子相关性。
- **回测分析**：比较策略与基准，检查回撤、换手、交易和持仓。
- **样本外检验**：检查 Walk-forward 各折选择和 OOS 表现。
- **方法与课程**：将页面指标对应到 `docs/factor_course/` 的课程。

## 5. 性能和数据边界

- 小型 CSV/JSON 根据文件修改时间缓存。
- 大型 `factor_scores.parquet` 使用 DuckDB，只读取所选因子和最新截面。
- 页面不公开本地路径选择器，也不提供写入数据、重跑研究或下单按钮。
- `reports/generated/` 默认不提交 Git；公开部署前必须单独设计脱敏示例数据或私有存储。

## 6. 故障排查

### 页面提示缺少研究文件

确认 `reports/generated/daily_factor_lab/summary.json` 等文件存在，然后重新运行每日流程。

### `No module named streamlit`

重新执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dashboard]"
```

### 页面数字和报告不一致

先确认数据截止日是否一致，然后比较 `summary.json`。看板不自行重算指标；若仍不一致，属于产物读取或展示错误，应停止使用并运行测试。
