"""Typed access to the single research configuration source."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from quant_lab.utils.config import load_config


@dataclass(frozen=True)
class FactorDefinition:
    name: str
    direction: float


@dataclass(frozen=True)
class ResearchSettings:
    raw_dir: str
    processed_file: str
    output_dir: str
    initial_calendar_days: int
    max_stale_trading_days: int
    required_latest_coverage: float
    min_listed_days: int
    min_amount: float
    require_known_status: bool
    forward_periods: int
    winsor_n_mad: float
    neutralize_size: bool
    neutralize_industry: bool
    groups: int
    factors: tuple[FactorDefinition, ...]
    rebalance_frequency: str
    top_n: int
    exit_rank: int
    max_weight: float
    backtest: dict[str, Any]
    paper_state_file: str
    next_orders_file: str
    history_start_date: str | None = None
    research_start_date: str | None = None
    membership_frequency: str = "W-FRI"
    minimum_valid_factors: int = 1
    minimum_factor_coverage: float = 0.0
    validation: dict[str, Any] = field(default_factory=dict)


def load_research_settings(path: str | Path) -> ResearchSettings:
    config = load_config(path)
    data = config["data"]
    universe = config["universe"]
    factor = config["factors"]
    portfolio = config["portfolio"]
    paper = config["paper_account"]
    definitions = tuple(
        FactorDefinition(str(item["name"]), float(item.get("direction", 1.0)))
        for item in factor["definitions"]
    )
    top_n = int(portfolio["top_n"])
    exit_rank = int(portfolio["exit_rank"])
    if top_n <= 0 or exit_rank < top_n:
        raise ValueError("Require exit_rank >= top_n > 0")
    minimum_valid = int(factor.get("minimum_valid_factors", 1))
    if not 1 <= minimum_valid <= len(definitions):
        raise ValueError("minimum_valid_factors must be within configured factors")
    minimum_coverage = float(factor.get("minimum_factor_coverage", 0.0))
    if not 0.0 <= minimum_coverage <= 1.0:
        raise ValueError("minimum_factor_coverage must be between zero and one")
    return ResearchSettings(
        raw_dir=str(data["raw_dir"]),
        processed_file=str(data["processed_file"]),
        output_dir=str(config["project"]["output_dir"]),
        initial_calendar_days=int(data["initial_calendar_days"]),
        max_stale_trading_days=int(data["max_stale_trading_days"]),
        required_latest_coverage=float(data["required_latest_coverage"]),
        min_listed_days=int(universe["min_listed_days"]),
        min_amount=float(universe["min_amount"]),
        require_known_status=bool(universe["require_known_status"]),
        forward_periods=int(factor["forward_periods"]),
        winsor_n_mad=float(factor["winsor_n_mad"]),
        neutralize_size=bool(factor["neutralize_size"]),
        neutralize_industry=bool(factor["neutralize_industry"]),
        groups=int(factor["groups"]),
        factors=definitions,
        rebalance_frequency=str(portfolio["rebalance_frequency"]),
        top_n=top_n,
        exit_rank=exit_rank,
        max_weight=float(portfolio["max_weight"]),
        backtest=dict(config["backtest"]),
        paper_state_file=str(paper["state_file"]),
        next_orders_file=str(paper["next_orders_file"]),
        history_start_date=(
            str(data["history_start_date"])
            if data.get("history_start_date") is not None
            else None
        ),
        research_start_date=(
            str(data["research_start_date"])
            if data.get("research_start_date") is not None
            else None
        ),
        membership_frequency=str(data.get("membership_frequency", "W-FRI")),
        minimum_valid_factors=minimum_valid,
        minimum_factor_coverage=minimum_coverage,
        validation=dict(config.get("validation", {})),
    )
