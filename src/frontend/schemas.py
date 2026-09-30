"""Pydantic mirrors of the wire contracts in ``src/contracts.py``.

``src/contracts.py`` (TypedDict, dependency-free) is the source of truth for
field names across the backend/frontend boundary. This module exists because
the frontend has one job TypedDicts cannot do: *validate* what Claude sends
back. Every model here carries the same keys as its TypedDict twin, and
``tests/test_frontend.py::test_pydantic_mirrors_typeddict_contracts`` fails the
build if they drift.

Closed vocabularies are imported from the contracts module rather than
retyped, so an operation or issue code cannot exist here and not there.

Every model sets ``extra="forbid"``. If the profiler ever grows a field that
carries cell content, validation fails here rather than leaking it into an API
payload. This is the first of two privacy interlocks; the second is
``sanitize_profile`` in ``mcp_planner.py``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.contracts import CLEANING_OPERATIONS, ISSUE_CODES

AutonomyTier = Literal["automatic", "review_required", "human_required"]
BusinessImpact = Literal["low", "high"]

#: Sorted so the Claude tool schema is byte-stable between runs (prompt caching).
OPERATIONS: tuple[str, ...] = tuple(sorted(CLEANING_OPERATIONS))
ISSUES: tuple[str, ...] = tuple(sorted(ISSUE_CODES))

#: ``impute_nulls`` strategies the backend cleaner accepts. Mirrors cleaner.py.
IMPUTE_STRATEGIES: tuple[str, ...] = ("median", "mean", "mode", "constant", "drop_rows")
CASING_MODES: tuple[str, ...] = ("lower", "upper", "title")
OUTLIER_METHODS: tuple[str, ...] = ("iqr", "zscore")


# --------------------------------------------------------------------------- #
# Interface 1 — DataProfile (backend -> frontend), consumed
# --------------------------------------------------------------------------- #


class NumericAggregates(BaseModel):
    """Present only for numeric *measure* columns. Never for identifiers."""

    model_config = ConfigDict(extra="forbid")

    min: float
    max: float
    mean: float
    median: float
    std: float


class ColumnProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    dtype: str
    null_count: int = Field(ge=0)
    null_pct: float = Field(ge=0.0, le=100.0)
    unique_count: int = Field(ge=0)
    is_numeric: bool
    issues_detected: list[str] = Field(default_factory=list)
    numeric: NumericAggregates | None = None

    @field_validator("issues_detected")
    @classmethod
    def _issues_in_vocabulary(cls, codes: list[str]) -> list[str]:
        unknown = sorted(set(codes) - ISSUE_CODES)
        if unknown:
            raise ValueError(f"unknown issue code(s) {unknown}; allowed: {ISSUES}")
        return codes


class DataProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_rows: int = Field(ge=0)
    total_columns: int = Field(ge=0)
    exact_duplicate_count: int = Field(ge=0)
    profiled_at: str
    columns: list[ColumnProfile]


# --------------------------------------------------------------------------- #
# Interface 2 — ProposedAction (frontend -> backend), produced
# --------------------------------------------------------------------------- #


class PlannedAction(BaseModel):
    """What Claude is allowed to emit: the judgement, not the permission.

    ``action_id`` is minted locally and ``autonomy_tier`` / ``requires_review``
    are derived by ``src.backend.policy`` - the model never supplies any of
    the three, so it never gets to grant itself a permission.
    """

    model_config = ConfigDict(extra="forbid")

    operation: str
    target_columns: list[str]
    parameters: dict[str, object] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
    business_impact: BusinessImpact
    rationale: str = Field(min_length=1)

    @field_validator("operation")
    @classmethod
    def _operation_in_vocabulary(cls, op: str) -> str:
        if op not in CLEANING_OPERATIONS:
            raise ValueError(f"unknown operation {op!r}; allowed: {OPERATIONS}")
        return op

    @model_validator(mode="after")
    def _targets_required_unless_whole_row(self) -> "PlannedAction":
        # Only whole-row deduplication may omit targets (contract §1B).
        if not self.target_columns and self.operation != "drop_exact_duplicates":
            raise ValueError(f"{self.operation} requires at least one target column")
        return self


class ProposedAction(PlannedAction):
    """The full contract object the backend consumes. Mirrors
    ``src.contracts.ProposedAction`` key-for-key."""

    action_id: str = Field(min_length=1)
    autonomy_tier: AutonomyTier
    requires_review: bool


class PlannerOutput(BaseModel):
    """Exactly the shape of the ``propose_execution_plan`` tool input."""

    model_config = ConfigDict(extra="forbid")

    dataset_summary: str = Field(min_length=1)
    actions: list[PlannedAction]
    analysis_questions: list[str] = Field(default_factory=list, max_length=5)


class ExecutionPlan(BaseModel):
    """The plan envelope. Frontend session state only - the backend never sees
    it; it consumes ``actions`` one at a time (contract §1B).

    A plan failing validation is discarded whole, never partially executed: a
    partially-valid plan means the model misunderstood the schema, which is
    precisely when its remaining actions deserve the least trust.
    """

    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(min_length=1)
    dataset_summary: str
    actions: list[ProposedAction]
    analysis_questions: list[str] = Field(default_factory=list, max_length=5)

    def actions_as_contract(self) -> list[dict]:
        """Plain dicts in ``src.contracts.ProposedAction`` shape for the backend."""
        return [a.model_dump() for a in self.actions]
