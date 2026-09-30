"""The autonomy policy matrix - the single definition of "may this run
without a human". The frontend imports it; nothing reimplements it.

Matrix (docs/DEVELOPMENT_PROPOSAL_HYBRID_AUTONOMY.md section 5.2):

                          low impact          high impact
    confidence >= 0.90    automatic           review_required
    confidence <  0.90    review_required     human_required

High confidence never buys a pass on high impact. A planner proposes and
scores; it does not decide its own permission level.
"""

from __future__ import annotations

from src.contracts import AutonomyTier, ProposedAction

#: Confidence at or above this is "high". The one tunable in the matrix.
CONFIDENCE_THRESHOLD: float = 0.90

_VALID_IMPACTS: frozenset[str] = frozenset({"low", "high"})


def classify(confidence: float, business_impact: str) -> AutonomyTier:
    """Map (confidence, impact) to a tier.

    Raises ``ValueError`` on anything malformed rather than defaulting. A bad
    proposal must never silently become "automatic".
    """
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError(f"confidence must be numeric, got {type(confidence).__name__}")
    if not 0.0 <= confidence <= 1.0:
        raise ValueError(f"confidence must be within [0.0, 1.0], got {confidence}")
    if business_impact not in _VALID_IMPACTS:
        raise ValueError(f"business_impact must be one of {sorted(_VALID_IMPACTS)}, got {business_impact!r}")

    high_confidence = confidence >= CONFIDENCE_THRESHOLD
    high_impact = business_impact == "high"

    if high_confidence and not high_impact:
        return "automatic"
    if not high_confidence and high_impact:
        return "human_required"
    return "review_required"


def requires_review(tier: str) -> bool:
    return tier != "automatic"


def annotate(action: ProposedAction) -> ProposedAction:
    """Return a copy of ``action`` with tier fields re-derived from its own
    confidence and impact, discarding whatever the caller sent."""
    tier = classify(float(action["confidence"]), str(action["business_impact"]))
    return {**action, "autonomy_tier": tier, "requires_review": requires_review(tier)}
