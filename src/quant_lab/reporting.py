"""Dependency-free SVG charts and Markdown research report rendering."""

from __future__ import annotations

import html
from collections.abc import Iterable
from pathlib import Path

import numpy as np
import pandas as pd


def write_line_svg(
    values: pd.Series,
    path: str | Path,
    title: str,
    width: int = 900,
    height: int = 320,
) -> None:
    clean = values.dropna().astype(float)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if clean.empty:
        output.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
        return
    left, right, top, bottom = 60, 20, 40, 40
    minimum, maximum = float(clean.min()), float(clean.max())
    if np.isclose(minimum, maximum):
        minimum -= 1.0
        maximum += 1.0
    x_span = width - left - right
    y_span = height - top - bottom
    points = []
    for index, value in enumerate(clean):
        x = left + x_span * index / max(1, len(clean) - 1)
        y = top + y_span * (maximum - value) / (maximum - minimum)
        points.append(f"{x:.1f},{y:.1f}")
    zero_y = top + y_span * (maximum - 0.0) / (maximum - minimum)
    zero_line = ""
    if minimum <= 0 <= maximum:
        zero_line = (
            f"<line x1='{left}' y1='{zero_y:.1f}' x2='{width - right}' "
            f"y2='{zero_y:.1f}' stroke='#bbb' stroke-dasharray='4 4'/>"
        )
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="white"/>
<text x="{left}" y="24" font-family="sans-serif" font-size="18">{html.escape(title)}</text>
<line x1="{left}" y1="{top}" x2="{left}" y2="{height - bottom}" stroke="#555"/>
<line x1="{left}" y1="{height - bottom}" x2="{width - right}" y2="{height - bottom}" stroke="#555"/>
{zero_line}
<polyline points="{" ".join(points)}" fill="none" stroke="#1769aa" stroke-width="2"/>
<text x="5" y="{top + 5}" font-family="monospace" font-size="12">{maximum:.4f}</text>
<text x="5" y="{height - bottom}" font-family="monospace" font-size="12">{minimum:.4f}</text>
</svg>"""
    output.write_text(svg, encoding="utf-8")


def markdown_table(frame: pd.DataFrame, columns: Iterable[str] | None = None) -> str:
    table = frame if columns is None else frame[list(columns)]
    if table.empty:
        return "_无数据_"
    formatted = table.copy()
    for column in formatted.select_dtypes(include=["number"]):
        formatted[column] = formatted[column].map(
            lambda value: "" if pd.isna(value) else f"{value:.4f}"
        )
    headers = [str(column) for column in formatted.columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    lines.extend(
        "| " + " | ".join(str(value) for value in row) + " |"
        for row in formatted.itertuples(index=False, name=None)
    )
    return "\n".join(lines)
