"""HITL review card.

Pure rendering. The tier is decided before this is called (``autonomy.py``),
the before/after frames are computed by the caller via ``cleaner.run`` and are
**local only** - they exist for the human's screen and must never enter a
Claude payload (contract §3).
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from src.backend.policy import CONFIDENCE_THRESHOLD

_TIER = {
    "automatic": ("✅", "Applied automatically", "Safe, reversible, and logged."),
    "review_required": ("⚠️", "Needs your approval", "Review the change below."),
    "human_required": ("⛔", "Blocked — needs a person", "Too uncertain and too costly to guess."),
}

_LABELS = {
    "drop_exact_duplicates": "Delete duplicate rows",
    "trim_whitespace": "Remove stray spaces",
    "normalize_casing": "Make letter case consistent",
    "standardize_dates": "Standardise date format",
    "impute_nulls": "Fill in missing values",
    "flag_outliers": "Flag unusual values",
}


def _confidence_badge(confidence: float) -> str:
    colour = "🟢" if confidence >= CONFIDENCE_THRESHOLD else "🟠"
    return f"{colour} {confidence:.0%} confident"


def _impact_badge(impact: str) -> str:
    return "🔴 High business impact" if impact == "high" else "⚪ Low business impact"


def _effect_badge(delta: Any) -> str:
    if delta is None:
        return ""
    if delta.rows_removed:
        return f"🗑️ {delta.rows_removed:,} row(s) removed"
    return f"✏️ {delta.cells_changed:,} cell(s) changed"


def render(
    action: dict[str, Any],
    tier: str,
    *,
    before: Any | None = None,
    after: Any | None = None,
    delta: Any | None = None,
    error: str | None = None,
    applied: bool = False,
    decision: str = "pending",
) -> str:
    """Render one card. Returns the decision after any button press.

    Decisions are returned rather than written to session state directly, so
    this stays a pure component and the caller owns state.
    """
    icon, headline, subtext = _TIER[tier]
    op = action["operation"]
    label = _LABELS.get(op, op)
    cols = action.get("target_columns") or []
    scope = "the whole table" if not cols else "**" + ", ".join(cols) + "**"

    with st.container(border=True):
        st.markdown(f"### {icon} {label}")
        st.caption(f"{headline} — {subtext}")
        st.markdown(f"Applies to {scope}")
        st.markdown(f"> {action['rationale']}")

        badges = st.columns(3)
        badges[0].markdown(_confidence_badge(float(action["confidence"])))
        badges[1].markdown(_impact_badge(str(action["business_impact"])))
        badges[2].markdown(_effect_badge(delta))

        params = action.get("parameters") or {}
        if params:
            st.caption("Settings: " + ", ".join(f"{k} = {v}" for k, v in sorted(params.items())))

        # ISS-006: a date column mixing real dates and junk must say so.
        if delta is not None and getattr(delta, "notes", None):
            for note in delta.notes:
                st.caption(f"ℹ️ {note}")

        if error:
            st.error(f"This step cannot run on the current data: {error}", icon="⚠️")
            return "rejected"

        if before is not None and after is not None:
            left, right = st.columns(2)
            left.caption("Before")
            left.dataframe(before, use_container_width=True, hide_index=True)
            right.caption("After")
            right.dataframe(after, use_container_width=True, hide_index=True)

        if applied:
            st.success("Applied. Undo from the Audit Trail tab.", icon="✅")
            return "approved"

        if tier == "human_required":
            st.warning(
                "This change is both uncertain and high-impact, so it will not be "
                "applied. Make it by hand if you want it.",
                icon="⛔",
            )
            return "rejected"

        if tier == "automatic":
            return "approved"

        approve, reject = st.columns(2)
        key = action["action_id"]
        if approve.button("Approve", key=f"approve_{key}", use_container_width=True, type="primary"):
            return "approved"
        if reject.button("Skip", key=f"reject_{key}", use_container_width=True):
            return "rejected"

    return decision
