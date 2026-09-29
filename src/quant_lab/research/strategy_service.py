"""Research-backed strategy execution, validation and immutable experiment save."""

import json
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

import yaml

from quant_lab.backtest.engine import BacktestConfig
from quant_lab.dashboard.experiments import ExperimentStore, frame_fingerprint
from quant_lab.dashboard.strategy_data import StrategyDataStore
from quant_lab.research.service import (
    ResearchRequest,
    code_version,
    dataset_version,
    factor_version,
    file_digest,
    local_path,
    run_research,
)
from quant_lab.strategy.sandbox import StrategySpec, run_strategy
from quant_lab.strategy.validation import robustness, walk_forward_strategy


def prepare_strategy_request(root: Path, payload: dict) -> dict:
    payload = deepcopy(payload)
    report = local_path(root, payload["report"], area="reports")
    provenance = json.loads((report / "dataset_provenance.json").read_text(encoding="utf-8"))
    if provenance.get("factor_version") != factor_version(root):
        raise ValueError("因子代码版本已变化或旧报告未记录版本，请先重新运行因子研究")
    if provenance.get("score_sha256") != file_digest(report / "factor_scores.parquet"):
        raise ValueError("因子分数文件与研究记录不一致，请重新运行研究")
    market = local_path(root, provenance["market_file"])
    version = dataset_version(market)
    if version["dataset_id"] != provenance["dataset_id"] or version["sha256"] != provenance.get("sha256"):
        raise ValueError("行情与研究分数版本不匹配，请重建研究")
    StrategySpec(**payload["strategy"])
    BacktestConfig(**payload["backtest"])
    payload.update(dataset=version, code_version=code_version(root), factor_version=factor_version(root),
                   market_file=provenance["market_file"])
    return payload


def run_strategy_request(root: Path, payload: dict, output: Path, progress) -> None:
    verified = prepare_strategy_request(root, payload)
    if verified != payload:
        raise ValueError("策略排队后版本发生变化，请重新提交")
    report = local_path(root, payload["report"], area="reports")
    source = json.loads((report / "dataset_provenance.json").read_text(encoding="utf-8"))
    spec = StrategySpec(**payload["strategy"])
    config = BacktestConfig(**payload["backtest"])
    if payload["universe"] != source["universe"]:
        progress("股票池已切换：重新计算该池的因子截面标准化")
        research_config = yaml.safe_load((report / "effective_config.yaml").read_text(encoding="utf-8"))
        research_config["universe"]["preferred_index"] = payload["universe"]
        research_config["data"]["research_start_date"] = payload["start"]
        research_config["data"]["research_end_date"] = payload["end"]
        report = output / "pool_research"
        run_research(root, ResearchRequest.create(root, research_config), report, progress)
        source = json.loads((report / "dataset_provenance.json").read_text(encoding="utf-8"))
    progress("加载与研究版本一致的行情及因子分数")
    data = StrategyDataStore(report / "factor_scores.parquet", local_path(root, payload["market_file"]))
    scores, market = data.load(list(spec.factor_weights), payload["start"], payload["end"],
                              allowed_factors=list(spec.factor_weights))
    progress("执行当前策略的下一交易日成交回测")
    result = run_strategy(scores, market, spec, config)
    validation = {"status": "not_requested", "message": "尚未进行样本外验证"}
    if payload.get("walk_forward", True):
        validation = walk_forward_strategy(scores, market, spec, config, output / "validation",
            **payload.get("validation", {}), progress=progress)
    comparisons = []
    if payload.get("robustness", True):
        comparisons = robustness(scores, market, spec, config, output / "robustness", progress)
    progress("核验版本，保存研究假设和实验结果")
    if prepare_strategy_request(root, payload) != payload:
        raise ValueError("研究过程中版本已变化；未发布本次实验")
    provenance = {**source, "universe": payload["universe"], "request": payload,
                  "code_version": payload["code_version"],
                  "requested_start": payload["start"], "requested_end": payload["end"],
                  "market_fingerprint": frame_fingerprint(market), "score_fingerprint": frame_fingerprint(scores),
                  "hypothesis": payload.get("hypothesis", ""), "conclusion": payload.get("conclusion", ""),
                  "validation": validation, "robustness": comparisons,
                  "execution_timing": "收盘后信号，下一交易日开盘；分红送转账本仍是研究近似"}
    storage = ExperimentStore(output / "experiments")
    identifier = storage.save(result, spec, config, provenance, payload.get("name", "策略实验"))
    (output / "experiment.json").write_text(json.dumps({"id": identifier, "validation": validation,
        "robustness": comparisons, "strategy": asdict(spec), "backtest": asdict(config)},
        ensure_ascii=False, indent=2), encoding="utf-8")
