"""Write an all-cases local research report, including negative and unfinished cases."""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd

from quant_lab.dashboard.experiments import ExperimentStore
from quant_lab.research.service import code_version, local_path

POOLS = {"000300.SH": "沪深300", "000905.SH": "中证500", "CSI800": "300与500并集"}
MODES = {"rank": "纯排名", "affordable": "预算筛选"}


def number(value, percent=False):
    if value is None or not math.isfinite(float(value)):
        return "—"
    return f"{value:.2%}" if percent else f"{value:.3f}"


def table(headers, rows):
    def cells(values):
        return "| " + " | ".join(str(v).replace("|", "\\|").replace("\n", " ") for v in values) + " |"
    return "\n".join([cells(headers), cells(["---"] * len(headers)), *[cells(row) for row in rows]])


def build_report(root: Path, campaign: Path) -> str:
    state = json.loads((campaign / "campaign.json").read_text(encoding="utf-8"))
    experiments = ExperimentStore(root / "data/state/strategy_experiments")
    records = {}
    for case in state["plan"]:
        item = state["cases"].get(case["key"], {})
        if item.get("status") == "succeeded":
            records[case["key"]] = experiments.metadata(item["experiment_id"])
    text = ["# 价量因子与5,000元账户：预先声明的完整对照\n",
            f"生成时间（UTC）：{datetime.now(timezone.utc).isoformat()}；已完成 {len(records)}/{len(state['plan'])} 项。",
            f"任务状态：{state['status']}。{state.get('reason', '')}",
            "这是工程验收与探索研究，已经观察过的历史区间不再是全新的未观察样本。所有案例按计划顺序列出，不按收益选优。",
            f"数据截至 {state['dataset']['end_date']}；数据SHA256 `{state['dataset']['sha256']}`；计算版本 `{state['code_version']}`。",
            "当前代码与这批实验一致。" if code_version(root) == state["code_version"] else "**历史计算版本**：当前代码已经变化，保留结果供复核；新实验必须记录新版本。",
            "默认对照：初始5,000元、5只目标、退出排名30、周频、单股20%上限、2%现金预留；科创板与创业板均关闭。各方案使用相同板块约束。",
            "分红送转尚未完整进入原始价格账本；下列账户收益是当前近似模型的输出。因子标签采用调整价，二者不能直接等同。",
            "## 全部账户结果\n"]
    rows = []
    for case in state["plan"]:
        record = records.get(case["key"])
        if record:
            metrics, diagnostics = record["metrics"], record["diagnostics"]
            values = ["完成", number(metrics["annual_return"], True), number(metrics["max_drawdown"], True),
                      number(diagnostics["average_holdings"]), number(diagnostics["average_cash_ratio"], True),
                      number(diagnostics["cost_to_initial_cash"], True), diagnostics["trade_count"], diagnostics["corporate_action_events"]]
        else:
            values = ["未完成", *["—"] * 7]
        rows.append([POOLS[case["pool"]], case["factor"], MODES[case["selection_mode"]], *values])
    text.append(table(["股票池", "因子", "选股", "状态", "年化收益", "最大回撤", "平均实际持股", "现金闲置", "费用/初始资金", "成交笔数", "除权息提示"], rows))
    text.append("费用列包括佣金、过户费及税，滑点已经进入成交价格；费用占初始资金可能超过100%，不等于单次费率超过100%。")
    text.append("## 同池同因子的预算筛选影响\n")
    paired = []
    for case in state["plan"]:
        if case["selection_mode"] != "rank":
            continue
        rank = records.get(case["key"])
        affordable = records.get(case["key"].removesuffix("rank") + "affordable")
        if not rank or not affordable:
            continue
        a, b = rank["diagnostics"], affordable["diagnostics"]
        paired.append([POOLS[case["pool"]], case["factor"],
                       number(b["average_cash_ratio"] - a["average_cash_ratio"], True),
                       number(b["average_holdings"] - a["average_holdings"]),
                       number(b["total_cost"] - a["total_cost"]),
                       number(affordable["metrics"]["annual_return"] - rank["metrics"]["annual_return"], True)])
    text.append(table(["股票池", "因子", "现金比例差", "实际持股数差", "累计费用差（元）", "年化收益差"], paired))
    text.append("差值均为预算筛选减纯排名。现金闲置减少只表示更多资金进入股票；候选股票、风险暴露和交易次数也可能改变，不能据此推断因子预测能力增强。")
    text.append("## 因子本身：次日开盘标签\n")
    for pool, entry in state["reports"].items():
        if "path" not in entry:
            continue
        folder = local_path(root, entry["path"], area="reports")
        summary = pd.read_csv(folder / "factor_summary.csv")
        text.append(f"### {POOLS[pool]}\n")
        text.append(table(["因子", "预设方向", "原始IC", "方向调整后IC", "原始IC HAC t", "95%近似区间", "有效日数"],
                          [[row.factor, row.configured_direction, number(row.next_open_mean_ic), number(row.next_open_oriented_ic),
                            number(row.hac_t), f"[{number(row.hac_ci_low)}, {number(row.hac_ci_high)}]", row.hac_observations]
                           for row in summary.itertuples()]))
    text.append("HAC处理标签重叠导致的序列相关；区间未做多重试验校正。因子IC不扣交易费用，也没有检验每只股票是否买得起。")
    text.append("## 每项实验的样本外区间与所有成本对照\n")
    for case in state["plan"]:
        record = records.get(case["key"])
        if not record:
            continue
        source = record["provenance"]
        text += [f"### {case['key']}\n", f"假设：{case['hypothesis']}",
                 f"实验编号 `{record['id']}`；实际区间 {record['actual_start']} 至 {record['actual_end']}。"]
        validation = source.get("validation", {})
        text.append(validation.get("method", validation.get("message", "未验证")))
        text.append(table(["测试起点", "测试终点", "训练IC", "验证IC", "年化收益", "最大回撤"],
                          [[fold["test_start"], fold["test_end"], number(fold["train_ic"]), number(fold["validation_ic"]),
                            number(fold["annual_return"], True), number(fold["max_drawdown"], True)] for fold in validation.get("folds", [])]))
        contrasts = source.get("robustness", [])
        if contrasts:
            text.append(table(["单因素对照", "状态", "年化收益", "平均现金比例", "费用/初始资金", "原因"],
                              [[v["case"], v["status"], number(v.get("annual_return"), True), number(v.get("average_cash_ratio"), True),
                                number(v.get("cost_to_initial_cash"), True), v.get("reason", "")] for v in contrasts]))
        else:
            text.append("本项未重复运行稳健性矩阵；计划对预算筛选方案运行全部对照。")
    text += ["## 接下来怎样判断\n",
             "先检查原始IC与预设方向，再看成本、现金闲置和逐年差异。若只在少数年份或某种费用假设下有效，应记录该限制。",
             "不能把这份报告中的最高收益项直接升级为实盘策略。先核对分红账本误差、代码版本和测试区间，再用未观察的未来数据做模拟观察。",
             "个人账户复核独立保存现金与完整持仓，行情更新成功后才显示当期清单；没有自动交易。"]
    return "\n\n".join(text) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", default="reports/generated/campaigns/price_volume_20260930")
    args = parser.parse_args()
    campaign = local_path(ROOT, args.campaign, area="reports/generated/campaigns")
    target = campaign / "RESEARCH_REPORT.md"
    target.write_text(build_report(ROOT, campaign), encoding="utf-8")
    print(target.relative_to(ROOT))


if __name__ == "__main__":
    main()
