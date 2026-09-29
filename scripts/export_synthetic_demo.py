"""Publish a small, synthetic-only comparison from two saved UI experiments."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_lab.dashboard.experiments import ExperimentStore
from quant_lab.research.service import dataset_version, local_path


def export_report(report: Path) -> None:
    """Preserve the original positional report-directory command."""
    source = json.loads((report / "dataset_provenance.json").read_text(encoding="utf-8"))
    if source.get("provider") != "synthetic":
        raise ValueError("Only synthetic reports may be exported for public demonstration")
    summary = json.loads((report / "summary.json").read_text(encoding="utf-8"))
    exported = {"synthetic": True, "warning": "仅用于流程演示，不能代表真实收益", "seed": 42,
                "symbols": 30, "generator": "quant_lab.research.service.create_demo",
                "factors": [f["name"] for f in summary["research_settings"]["factors"]], "universe": source["universe"],
                **{key: summary[key] for key in ("research_start_date", "end_date", "factor_count", "portfolio", "composite")}}
    graphic = (report / "equity.svg").read_bytes()
    destination = ROOT / "examples/synthetic_demo"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "summary.json").write_text(json.dumps(exported, ensure_ascii=False, indent=2), encoding="utf-8")
    (destination / "equity.svg").write_bytes(graphic)
    (destination / "README.md").write_text("# 合成数据演示\n\n全部数字由固定随机种子生成，不代表真实投资业绩。\n\n这是单份研究报告汇总，参数见summary.json。\n\n![合成演示净值](equity.svg)\n", encoding="utf-8")
    print("Exported synthetic aggregate report to examples/synthetic_demo")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", nargs="?", help="Legacy: completed synthetic research report directory")
    parser.add_argument("--experiments", nargs=2, metavar=("FIRST", "SECOND"))
    args = parser.parse_args()
    if bool(args.report) == bool(args.experiments):
        parser.error("Provide either a report directory or --experiments FIRST SECOND")
    if args.report:
        export_report(local_path(ROOT, args.report, area="reports"))
        return
    store = ExperimentStore(ROOT / "data/state/strategy_experiments")
    records = [store.metadata(identifier) for identifier in args.experiments]
    for record in records:
        source = record["provenance"]
        if source.get("provider") != "synthetic":
            raise ValueError("Public demo export accepts synthetic experiments only")
        version = dataset_version(local_path(ROOT, source["market_file"]))
        if version["provider"] != "synthetic" or version["sha256"] != source.get("sha256"):
            raise ValueError("Synthetic data source or fingerprint does not match")
    first, second = records
    for key in ("strategy", "actual_start", "actual_end"):
        if first[key] != second[key]:
            raise ValueError("Only initial cash may differ between demonstration experiments")
    for key in ("dataset_id", "market_fingerprint", "score_fingerprint", "code_version"):
        if not first["provenance"].get(key) or first["provenance"][key] != second["provenance"].get(key):
            raise ValueError("Demonstration inputs and computation versions must match")
    costs = [{key: value for key, value in record["backtest"].items() if key != "initial_cash"} for record in records]
    if costs[0] != costs[1] or first["backtest"]["initial_cash"] == second["backtest"]["initial_cash"]:
        raise ValueError("Keep fees fixed and choose two different starting cash amounts")

    # Plot is an optional export dependency; the daily dashboard does not need it.
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    plt.rcParams.update({"svg.hashsalt": "a-share-factor-lab-synthetic", "font.size": 10})
    figure, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True, constrained_layout=True)
    public = {"provider": "synthetic", "warning": "合成数据：只验证工程流程，不能证明真实收益。",
              "start": first["actual_start"], "end": first["actual_end"], "strategy": first["strategy"],
              "costs": costs[0], "source_code_version": first["provenance"]["code_version"],
              "runtime": first["provenance"].get("runtime"), "cases": []}
    rows = []
    for record, color in zip(records, ("#1768AC", "#D05A35")):
        cash = record["backtest"]["initial_cash"]
        curve = store.table(record["id"], "equity")
        nav = curve.equity / cash
        label = f"Synthetic | initial CNY {cash:,.0f}"
        axes[0].plot(curve.trade_date, nav, color=color, label=label, linewidth=1.2)
        axes[1].plot(curve.trade_date, curve.equity / curve.equity.cummax() - 1, color=color, linewidth=1.0)
        axes[2].plot(curve.trade_date, curve.cash / curve.equity, color=color, linewidth=.8, alpha=.8)
        diagnostics = record["diagnostics"]
        public["cases"].append({"initial_cash": cash, "metrics": record["metrics"], "diagnostics": diagnostics})
        rows.append(f"| {cash:,.0f} | {diagnostics['average_holdings']:.2f} | {diagnostics['average_cash_ratio']:.1%} | {diagnostics['cost_to_initial_cash']:.1%} | {diagnostics['trade_count']} |")
    axes[0].set_ylabel("NAV (initial = 1)")
    axes[0].legend(frameon=False, loc="upper left")
    axes[1].set_ylabel("Drawdown")
    axes[2].set_ylabel("Cash / equity")
    for axis in axes[1:]:
        axis.yaxis.set_major_formatter(PercentFormatter(1))
    for axis in axes:
        axis.grid(alpha=.2)
        axis.spines[["top", "right"]].set_visible(False)
    axes[-1].xaxis.set_major_locator(mdates.YearLocator(2))
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    figure.suptitle("Synthetic workflow demonstration — NOT real market performance", fontsize=13)
    destination = ROOT / "examples/synthetic_demo"
    destination.mkdir(parents=True, exist_ok=True)
    figure.savefig(destination / "equity.svg", metadata={"Date": None, "Creator": "A-Share Factor Lab synthetic demonstration"})
    graphic = destination / "equity.svg"
    graphic.write_text("\n".join(line.rstrip() for line in graphic.read_text(encoding="utf-8").splitlines()) + "\n", encoding="utf-8")
    plt.close(figure)
    (destination / "comparison.json").write_text(json.dumps(public, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    description = f"""# 合成数据演示：只改变资金，结果也会变化

**全部行情由固定随机种子生成，不代表真实投资业绩。** 此案例展示整数股、最低佣金、
预算筛选与现金闲置如何影响同一规则；不能据此判断投入更多或更少资金会获得更高收益。

策略为收盘位置因子 `range_position_20`、Top5、退出排名30、周频、单股上限20%、
2%现金预留与可负担候选筛选。两个实验使用相同数据、代码、因子、费用和交易规则，
仅初始资金不同。区间：{public['start']} 至 {public['end']}。

![合成数据净值、回撤与现金比例对照](equity.svg)

| 假设初始资金（元） | 平均实际持股 | 平均现金闲置 | 累计费用/初始资金 | 成交笔数 |
| --- | --- | --- | --- | --- |
{chr(10).join(rows)}

费用比例是整个区间累计值，不是单次费率。现金和持仓路径可能因资金不同而变化，
所以不能把一条净值曲线简单乘二，代替更大账户的回测。

复现：[使用教程](../../docs/USER_GUIDE.md) → [可运行练习](../../docs/BEGINNER_COURSE.md)。
网页选择合成演示、`range_position_20`、股票池并集，并按上述区间和参数分别运行5,000元与10,000元。
开启本策略样本外验证和全部单因素对照，再到实验历史检查实际成交。

也可运行 `scripts/verify_dashboard_flow.py` 完成同一路线。安装可选绘图依赖后，
使用 `scripts/export_synthetic_demo.py --experiments <第一个编号> <第二个编号>` 重新导出；
导出器会拒绝真实数据来源和版本、策略、费用不一致的两组结果。公开JSON只保留计算参数与汇总，
不导出实验备注、账户或原始行情。源码指纹与依赖版本记录在同目录 `comparison.json`。
本页对照以 `comparison.json` 为准；`summary.json` 保留此前单份研究报告的汇总，属于另一组配置。
旧命令 `scripts/export_synthetic_demo.py <合成报告目录>` 仍可导出单份报告，不需要绘图依赖。

练习：解释现金闲置、持股数和费用为什么变化；从成交回放找出一笔无法买入的案例，
然后用“账本核对”确认现金与股数相符。合成案例通过只说明流程可运行，不是因子有效性证据。
"""
    (destination / "README.md").write_text(description, encoding="utf-8")
    print("Exported synthetic-only README, SVG and aggregate comparison JSON")


if __name__ == "__main__":
    main()
