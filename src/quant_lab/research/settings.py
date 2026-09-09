"""Typed access to the single research configuration source."""

from __future__ import annotations

from dataclasses import dataclass
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
    )
