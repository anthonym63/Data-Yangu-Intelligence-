"""The autonomy matrix. Four quadrants plus the boundary plus bad input."""

from __future__ import annotations

import pytest

from src.backend import policy
from tests.conftest import make_action


@pytest.mark.parametrize(
    ("confidence", "impact", "expected"),
    [
        (0.95, "low", "automatic"),
        (0.95, "high", "review_required"),  # the load-bearing case
        (0.50, "low", "review_required"),
        (0.50, "high", "human_required"),
        (0.90, "low", "automatic"),  # inclusive boundary
        (0.90, "high", "review_required"),
        (0.8999, "low", "review_required"),
        (0.8999, "high", "human_required"),
        (1.0, "high", "review_required"),
        (0.0, "low", "review_required"),
    ],
)
def test_matrix(confidence, impact, expected):
    assert policy.classify(confidence, impact) == expected


def test_high_confidence_never_auto_runs_high_impact():
    for c in (0.9, 0.95, 0.99, 1.0):
        assert policy.classify(c, "high") != "automatic"


@pytest.mark.parametrize("bad", [-0.1, 1.1, "0.9", None, True])
def test_rejects_bad_confidence(bad):
    with pytest.raises(ValueError):
        policy.classify(bad, "low")


@pytest.mark.parametrize("bad", ["medium", "", None, "HIGH"])
def test_rejects_bad_impact(bad):
    with pytest.raises(ValueError):
        policy.classify(0.95, bad)


def test_requires_review():
    assert policy.requires_review("automatic") is False
    assert policy.requires_review("review_required") is True
    assert policy.requires_review("human_required") is True


def test_annotate_overrides_incoming_tier():
    action = make_action("trim_whitespace", ["x"])
    action.update(confidence=0.3, business_impact="high", autonomy_tier="automatic", requires_review=False)
    out = policy.annotate(action)
    assert out["autonomy_tier"] == "human_required" and out["requires_review"] is True
    assert action["autonomy_tier"] == "automatic"  # input untouched
