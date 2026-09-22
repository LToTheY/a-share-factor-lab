"""Shared display helpers for dashboard pages."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go


def pct(value: object, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}%}"
    except (TypeError, ValueError):
        return "—"


def number(value: object, digits: int = 2) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def equity_figure(strategy: pd.DataFrame, benchmark: pd.DataFrame, title: str) -> go.Figure:
    figure = go.Figure()
    for frame, label, color in (
        (strategy, "策略", "#d1495b"),
        (benchmark, "基准", "#2f6690"),
    ):
        if frame.empty:
            continue
        values = frame["equity"] / frame["equity"].iloc[0]
        figure.add_trace(
            go.Scatter(
                x=frame["trade_date"],
                y=values,
                name=label,
                mode="lines",
                line={"color": color, "width": 2},
            )
        )
    figure.update_layout(
        title=title,
        yaxis_title="累计净值（起点=1）",
        xaxis_title="交易日",
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.08},
        margin={"l": 20, "r": 20, "t": 60, "b": 20},
    )
    return figure


def drawdown_figure(strategy: pd.DataFrame) -> go.Figure:
    values = strategy["equity"].astype(float)
    drawdown = values / values.cummax() - 1.0
    figure = go.Figure(
        go.Scatter(
            x=strategy["trade_date"],
            y=drawdown,
            fill="tozeroy",
            line={"color": "#7f1d1d"},
            name="策略回撤",
        )
    )
    figure.update_layout(
        title="策略回撤",
        yaxis_tickformat=".0%",
        hovermode="x unified",
        margin={"l": 20, "r": 20, "t": 50, "b": 20},
    )
    return figure
