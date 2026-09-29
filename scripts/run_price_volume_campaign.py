"""Run a declared, resumable research matrix without selecting a winning test."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd
import yaml

from quant_lab.backtest.engine import BacktestConfig
from quant_lab.dashboard.experiments import ExperimentStore
from quant_lab.data.process_lock import data_lock
from quant_lab.data.storage_budget import directory_bytes
from quant_lab.research.jobs import JobStore, atomic_json
from quant_lab.research.service import code_version, dataset_version
from quant_lab.research.strategy_service import prepare_strategy_request
from quant_lab.strategy.sandbox import StrategySpec


def build_plan():
    hypotheses = {
        "momentum_60_5": "跳过最近5日的中期趋势，在费用与资金限制后仍可能延续；允许失败。",
        "reversal_5": "短期价格反转在成本后可能仍存在；与动量作机制对照。",
        "volatility_20": "较低日收益波动的股票可能有较稳健表现；检查是否只是低风险暴露。",
    }
    return [{"key": f"{pool}_{factor}_{mode}", "pool": pool, "factor": factor,
             "selection_mode": mode, "hypothesis": hypothesis}
            for pool in ("000300.SH", "000905.SH", "CSI800")
            for factor, hypothesis in hypotheses.items()
            for mode in ("rank", "affordable")]


def wait_for_job(storage, identifier, deadline_seconds=5400):
    deadline, last = time.monotonic() + deadline_seconds, None
    read_failures = 0
    while time.monotonic() < deadline:
        try:
            status = storage.get(identifier)
            read_failures = 0
        except PermissionError:
            read_failures += 1
            if read_failures > 10:
                raise
            print(f"{identifier[:8]}: 状态文件短暂被占用，等待后重读（{read_failures}/10）", flush=True)
            time.sleep(.2)
            continue
        message = status.get("message")
        if message != last:
            print(f"{identifier[:8]}: {message}", flush=True)
            last = message
        if status["state"] not in {"queued", "running", "cancelling"}:
            if status["state"] != "succeeded":
                raise RuntimeError(f"{identifier}: {status['state']}: {message}")
            return status
        time.sleep(2)
    storage.cancel(identifier)
    raise TimeoutError("单项任务超过90分钟，已请求取消；运行记录可继续检查")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", default="price_volume_20260930")
    parser.add_argument("--growth-budget-mb", type=int, default=900)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", args.id) or args.growth_budget_mb < 1:
        parser.error("Use a short local identifier and a positive growth budget")
    output = ROOT / "reports/generated/campaigns" / args.id
    output.mkdir(parents=True, exist_ok=True)
    state_file = output / "campaign.json"
    config = yaml.safe_load((ROOT / "configs/research.yaml").read_text(encoding="utf-8"))
    dataset = dataset_version(ROOT / config["data"]["processed_file"])
    version = code_version(ROOT)
    def used_bytes():
        return directory_bytes(ROOT / "data") + directory_bytes(ROOT / "reports")
    if state_file.exists():
        state = json.loads(state_file.read_text(encoding="utf-8"))
        if state["code_version"] != version or state["dataset"] != dataset or state["plan"] != build_plan():
            raise ValueError("代码、数据或预先声明的实验矩阵已变化；请使用新编号，保留原结果")
        if state.get("reason"):
            state.setdefault("interruptions", []).append({"resumed_at": datetime.now(timezone.utc).isoformat(), "reason": state.pop("reason")})
        state["status"] = "running"
    else:
        state = {"id": args.id, "created_at": datetime.now(timezone.utc).isoformat(),
                 "code_version": version, "dataset": dataset, "plan": build_plan(),
                 "initial_bytes": used_bytes(), "growth_budget": args.growth_budget_mb * 1_000_000,
                 "reports": {}, "cases": {}, "status": "running",
                 "interpretation": "历史区间已经被研究，仅为探索与工程验收；全部案例预先声明，不能当作新的未观察样本外证据"}
        atomic_json(state_file, state)
    jobs = JobStore(ROOT)
    experiments = ExperimentStore(ROOT / "data/state/strategy_experiments")
    with data_lock(ROOT / "data/state/price_volume_campaign.lock"):
        try:
            for pool in ("000300.SH", "000905.SH", "CSI800"):
                if used_bytes() > state["initial_bytes"] + state["growth_budget"]:
                    raise RuntimeError("新增文件达到本次预算；保留已完成部分，停止后续大任务")
                if pool not in state["reports"]:
                    selected = deepcopy(config)
                    selected["universe"]["preferred_index"] = pool
                    selected["factors"]["definitions"] = [{"name": name, "direction": direction} for name, direction in (
                        ("momentum_60_5", 1.), ("reversal_5", 1.), ("volatility_20", -1.),
                        ("downside_volatility_20", -1.), ("overnight_reversal_5", 1.), ("intraday_momentum_20", 1.))]
                    selected["factors"]["minimum_valid_factors"] = 5
                    state["reports"][pool] = {"job": jobs.submit_research(selected)["id"]}
                    atomic_json(state_file, state)
                report_status = wait_for_job(jobs, state["reports"][pool]["job"])
                state["reports"][pool]["path"] = report_status["result"]
                for case in [p for p in state["plan"] if p["pool"] == pool]:
                    if used_bytes() > state["initial_bytes"] + state["growth_budget"]:
                        raise RuntimeError("新增文件达到本次预算，停止后续任务")
                    key = case["key"]
                    if key not in state["cases"]:
                        spec = StrategySpec({case["factor"]: 1.}, top_n=5, exit_rank=30, max_weight=.2,
                                            selection_mode=case["selection_mode"], cash_buffer=.02,
                                            allow_star=False, allow_chinext=False)
                        payload = {"name": key, "hypothesis": case["hypothesis"], "report": report_status["result"],
                                   "universe": pool, "start": "2016-01-01", "end": dataset["end_date"],
                                   "strategy": asdict(spec), "backtest": asdict(BacktestConfig(initial_cash=5000)),
                                   "walk_forward": True, "robustness": case["selection_mode"] == "affordable"}
                        request = prepare_strategy_request(ROOT, payload)
                        state["cases"][key] = {"job": jobs.submit("strategy", request)["id"]}
                        atomic_json(state_file, state)
                    status = wait_for_job(jobs, state["cases"][key]["job"])
                    result = json.loads((ROOT / status["result"] / "experiment.json").read_text(encoding="utf-8"))
                    record = experiments.metadata(result["id"])
                    state["cases"][key].update(experiment_id=result["id"], status="succeeded",
                                               metrics=record["metrics"], diagnostics=record["diagnostics"],
                                               validation_status=result["validation"]["status"])
                    atomic_json(state_file, state)
                    rows = [{**p, **{k: v for k, v in state["cases"].get(p["key"], {}).items() if k not in {"metrics", "diagnostics"}},
                             **state["cases"].get(p["key"], {}).get("metrics", {}), **state["cases"].get(p["key"], {}).get("diagnostics", {})} for p in state["plan"]]
                    pd.DataFrame(rows).to_csv(output / "comparison.csv", index=False, encoding="utf-8-sig")
            state.update(status="succeeded", completed_at=datetime.now(timezone.utc).isoformat(), final_bytes=used_bytes())
        except Exception as exc:
            state.update(status="stopped", reason=str(exc))
            raise
        finally:
            atomic_json(state_file, state)
    print(json.dumps({"status": state["status"], "cases": len(state["cases"]), "output": str(output.relative_to(ROOT))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
