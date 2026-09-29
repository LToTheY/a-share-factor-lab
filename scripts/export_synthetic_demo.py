"""Export a deliberately small, synthetic-only public example."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_lab.research.service import local_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", help="Completed synthetic report directory")
    args = parser.parse_args()
    report = local_path(ROOT, args.report, area="reports")
    source = json.loads((report / "dataset_provenance.json").read_text(encoding="utf-8"))
    if source.get("provider") != "synthetic":
        raise ValueError("Only synthetic reports may be exported for public demonstration")
    summary = json.loads((report / "summary.json").read_text(encoding="utf-8"))
    exported = {"synthetic": True, "warning": "仅用于流程演示，不能代表真实收益", "seed": 42,
                "symbols": 30, "generator": "quant_lab.research.service.create_demo",
                "factors": [f["name"] for f in summary["research_settings"]["factors"]], "universe": source["universe"],
                **{key: summary[key] for key in ("research_start_date", "end_date", "factor_count", "portfolio", "composite")}}
    # Whitelist aggregate fields: no local paths, raw observations or account records.
    out = ROOT / "examples/synthetic_demo"
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(exported, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "equity.svg").write_bytes((report / "equity.svg").read_bytes())
    (out / "README.md").write_text("# 合成数据演示\n\n全部数字由固定随机种子生成，不代表真实投资业绩。\n\n网页选择合成演示、range_position_20、历史池并集、2016-01-01至2024-12-18即可重跑。\n\n![合成演示净值](equity.svg)\n", encoding="utf-8")
    print("Exported synthetic aggregate example to examples/synthetic_demo")


if __name__ == "__main__":
    main()
