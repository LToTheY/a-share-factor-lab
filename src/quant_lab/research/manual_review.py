"""Freeze an experiment's rules for fresh-data, manual-account review only."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import yaml

from quant_lab.dashboard.experiments import ExperimentStore
from quant_lab.portfolio.order_plan import validate_account
from quant_lab.research.service import factor_version, file_digest, local_path
from quant_lab.strategy.sandbox import StrategySpec


def profile_path(root: Path, identifier: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", identifier):
        raise ValueError("无效人工复核方案编号")
    return root / "data/state/manual_review" / identifier


def resolve_report(root: Path, provenance: dict) -> Path:
    """Locate an exact score snapshot even after the default report was archived."""
    original = provenance.get("request", {}).get("report")
    candidates = [local_path(root, original, area="reports")] if original else []
    candidates += [p.parent for p in (root / "reports").rglob("dataset_provenance.json")
                   if not any(part.startswith(".pending") for part in p.parts)]
    for folder in dict.fromkeys(candidates):
        sidecar = folder / "dataset_provenance.json"
        if not sidecar.is_file() or not (folder / "effective_config.yaml").is_file():
            continue
        try:
            source = json.loads(sidecar.read_text(encoding="utf-8"))
            if (source.get("score_sha256") and source.get("score_sha256") == provenance.get("score_sha256")
                    and source.get("dataset_id") == provenance.get("dataset_id")
                    and file_digest(folder / "factor_scores.parquet") == source["score_sha256"]):
                return folder
        except (ValueError, OSError):
            continue
    raise ValueError("找不到该实验对应的完整因子研究快照，请重跑研究后创建新实验")


def create_profile(root: Path, experiment_id: str, account: dict) -> dict:
    """Create local copies; never alter default research config or paper holdings."""
    metadata = ExperimentStore(root / "data/state/strategy_experiments").metadata(experiment_id)
    source = metadata["provenance"]
    if source.get("provider") != "csmar_baostock":
        raise ValueError("人工交易复核只接受已核验真实数据实验；合成或未知来源不能作为交易依据")
    if source.get("factor_version") != factor_version(root):
        raise ValueError("因子代码已变化，请重新研究并验证策略后再建立复核方案")
    spec = StrategySpec(**metadata["strategy"])
    checked = validate_account(account)
    if not checked["positions"] and checked["cash"] <= 0:
        raise ValueError("请填写非零现金或实际持仓")
    if any(not re.fullmatch(r"(?:6\d{5}\.SH|[03]\d{5}\.SZ)", s) for s in checked["positions"]):
        raise ValueError("持仓代码格式为 000001.SZ 或 600000.SH；当前只支持沪深A股")
    report = resolve_report(root, source)
    config = yaml.safe_load((report / "effective_config.yaml").read_text(encoding="utf-8"))
    config = deepcopy(config)
    active = {name for name, weight in spec.factor_weights.items() if weight}
    definitions = [d for d in config["factors"]["definitions"] if d["name"] in active]
    if {d["name"] for d in definitions} != active:
        raise ValueError("实验因子与源配置不一致")
    config["factors"].update(definitions=definitions, minimum_valid_factors=1)
    config["universe"]["preferred_index"] = source["universe"]
    config["portfolio"].update(top_n=spec.top_n, exit_rank=spec.exit_rank,
                              rebalance_frequency=spec.rebalance_frequency, max_weight=spec.max_weight)
    config["backtest"] = metadata["backtest"]
    config["signal_strategy"] = metadata["strategy"]
    identifier = uuid4().hex
    folder = profile_path(root, identifier)
    state_path = folder / "account_snapshot.json"
    config["paper_account"]["state_file"] = str(state_path.relative_to(root))
    config["paper_account"]["next_orders_file"] = f"reports/generated/manual_review/{identifier}/next_day_orders.csv"
    profile = {"id": identifier, "name": metadata["name"], "experiment_id": experiment_id,
               "created_at": datetime.now(timezone.utc).isoformat(),
               "factor_version": source["factor_version"], "code_version_at_experiment": source.get("code_version"),
               "historical_validation": source.get("validation", {}),
               "research_metrics": metadata.get("metrics", {}),
               "research_diagnostics": metadata.get("diagnostics", {}),
               "research_initial_cash": metadata.get("backtest", {}).get("initial_cash"),
               "config_file": str((folder / "research.yaml").relative_to(root)),
               "account_file": str(state_path.relative_to(root)),
               "account_type": "manual_snapshot", "automatic_trading": False}
    folder.mkdir(parents=True)
    state_path.write_text(json.dumps(checked, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "research.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    (folder / "profile.json").write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return profile


def load_profile(root: Path, identifier: str) -> dict:
    value = json.loads((profile_path(root, identifier) / "profile.json").read_text(encoding="utf-8"))
    if value.get("id") != identifier or value.get("factor_version") != factor_version(root):
        raise ValueError("方案编号或因子版本不匹配，请重新研究并建立方案")
    return value
