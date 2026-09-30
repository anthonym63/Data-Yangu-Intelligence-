"""Frontend view of the autonomy policy.

The matrix itself lives in ``src.backend.policy`` and is not reimplemented here
(contract §1D: "the frontend imports it; nothing reimplements it"). This module
adds the one thing that is purely a UI concern: the Strict Human Review toggle,
which demotes every ``automatic`` tier to ``review_required``. Strict mode can
only ever add friction, never remove it.
"""

from __future__ import annotations

from typing import Any

from src.backend.policy import annotate

STRICT = "strict"
SEMI = "semi"


def apply_strict_mode(tier: str, strict: bool) -> str:
    if strict and tier == "automatic":
        return "review_required"
    return tier


def effective_tier(action: dict[str, Any], *, strict: bool) -> str:
    """Re-derive the tier from the action's own confidence and impact - never
    trusting whatever the action carries - then apply the UI toggle."""
    return apply_strict_mode(annotate(action)["autonomy_tier"], strict)
