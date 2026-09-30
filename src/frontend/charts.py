"""Plotly figure builders over Agent 1's aggregated DuckDB output.

Builders return Figure objects and never call ``st.plotly_chart`` themselves,
which keeps them testable without a Streamlit runtime.

Input is a list of row mappings - the shape DuckDB's ``.fetchall()`` plus column
names naturally produces - so this module stays free of any Polars or DuckDB
import and can be exercised with plain dicts.
"""

from typing import Any

import plotly.graph_objects as go

Rows = list[dict[str, Any]]

#: Colour-blind-safe qualitative palette, applied consistently across every
#: chart so a series keeps its colour between views.
PALETTE = ["#4C78A8", "#F58518", "#54A24B", "#E45756", "#79706E", "#B279A2"]

_LAYOUT = {
    "template": "plotly_white",
    "margin": {"l": 56, "r": 24, "t": 56, "b": 48},
    "colorway": PALETTE,
    "font": {"size": 13},
    "hoverlabel": {"font": {"size": 12}},
}


def _series(rows: Rows, key: str) -> list[Any]:
    if not rows:
        return []
    if key not in rows[0]:
        raise KeyError(f"Column {key!r} not present in result rows: {sorted(rows[0])}")
    return [row.get(key) for row in rows]


def _finish(fig: go.Figure, title: str, x_title: str, y_title: str) -> go.Figure:
    fig.update_layout(title=title, **_LAYOUT)
    fig.update_xaxes(title_text=x_title, showgrid=False)
    fig.update_yaxes(title_text=y_title, gridcolor="#E8E8E8", zerolinecolor="#E8E8E8")
    return fig


def bar_chart(rows: Rows, x: str, y: str, title: str = "") -> go.Figure:
    """Categorical comparison. Sorted descending so the ranking reads directly."""
    pairs = sorted(zip(_series(rows, x), _series(rows, y)), key=lambda p: (p[1] is None, -(p[1] or 0)))
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    fig = go.Figure(go.Bar(x=xs, y=ys, marker_color=PALETTE[0]))
    return _finish(fig, title or f"{y} by {x}", x, y)


def line_chart(rows: Rows, x: str, y: str, title: str = "") -> go.Figure:
    """Trend over an ordered dimension, typically time."""
    fig = go.Figure(
        go.Scatter(x=_series(rows, x), y=_series(rows, y), mode="lines+markers",
                   line={"color": PALETTE[0], "width": 2})
    )
    return _finish(fig, title or f"{y} over {x}", x, y)


def scatter_chart(rows: Rows, x: str, y: str, title: str = "") -> go.Figure:
    """Relationship between two numeric columns."""
    fig = go.Figure(
        go.Scatter(x=_series(rows, x), y=_series(rows, y), mode="markers",
                   marker={"color": PALETTE[0], "size": 8, "opacity": 0.7})
    )
    return _finish(fig, title or f"{y} vs {x}", x, y)
