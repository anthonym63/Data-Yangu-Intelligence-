"""File Summary sidebar and the autonomy mode control."""

from __future__ import annotations

from typing import Any

import streamlit as st

from ..autonomy import SEMI, STRICT


def render(profile: dict[str, Any] | None, filename: str | None) -> str:
    """Render the sidebar. Returns the selected autonomy mode."""
    with st.sidebar:
        st.subheader("File summary")

        if profile is None:
            st.caption("No file loaded yet.")
        else:
            st.caption(filename or "unnamed file")
            left, right = st.columns(2)
            left.metric("Rows", f"{profile['total_rows']:,}")
            right.metric("Columns", profile["total_columns"])

            issue_count = sum(len(c["issues_detected"]) for c in profile["columns"])
            affected = sum(1 for c in profile["columns"] if c["issues_detected"])
            left, right = st.columns(2)
            left.metric("Quality issues", issue_count, help=f"Across {affected} of {profile['total_columns']} columns.")
            right.metric("Duplicate rows", f"{profile['exact_duplicate_count']:,}")

        st.divider()
        st.subheader("Autonomy")
        mode = st.radio(
            "How much should run without you?",
            options=[STRICT, SEMI],
            format_func=lambda v: {
                STRICT: "Strict — review everything",
                SEMI: "Semi-autonomous — auto-apply safe steps",
            }[v],
            key="autonomy_mode",
        )
        st.caption(
            "Strict mode sends every proposed change to the review queue, "
            "including ones the system is confident about."
        )

        st.divider()
        st.caption(
            "🔒 Your data stays on this machine. The AI only ever receives "
            "column names, types and summary statistics — never your records."
        )

    return mode
