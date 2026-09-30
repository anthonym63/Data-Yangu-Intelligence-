"""Wire contracts between the backend (this team) and the frontend team.

The backend PRODUCES ``DataProfile`` and ``AuditEntry`` and CONSUMES
``ProposedAction``. These shapes are the handshake; change them only with the
frontend team's agreement.

Privacy contract: no type in this module may carry a cell value from the source
dataset. ``DataProfile`` is the only object the frontend is allowed to forward
to an LLM, so what is absent here is what makes the platform's privacy claim
true. See docs/implementations/PHASE_1_BACKEND.md section 2.

No third-party dependencies, on purpose: either team can import this without
pulling in the other's stack. Python 3.10 floor (no ``typing.NotRequired``).
"""

from typing import Literal, TypedDict

AutonomyTier = Literal["automatic", "review_required", "human_required"]
BusinessImpact = Literal["low", "high"]
HumanDecision = Literal["pending", "approved", "rejected", "edited"]

CleaningOperation = Literal[
    "drop_exact_duplicates",
    "trim_whitespace",
    "standardize_dates",
    "impute_nulls",
    "normalize_casing",
    "flag_outliers",
]

#: Runtime-checkable form of ``CleaningOperation`` (``Literal`` can't be
#: membership-tested). The cleaner rejects anything outside this set.
CLEANING_OPERATIONS: frozenset[str] = frozenset(
    {
        "drop_exact_duplicates",
        "trim_whitespace",
        "standardize_dates",
        "impute_nulls",
        "normalize_casing",
        "flag_outliers",
    }
)

IssueCode = Literal[
    "mixed_date_formats",
    "inconsistent_casing",
    "leading_trailing_whitespace",
    "high_null_rate",
    "mixed_numeric_and_text",
    "possible_duplicate_key",
    "numeric_outliers",
]

ISSUE_CODES: frozenset[str] = frozenset(
    {
        "mixed_date_formats",
        "inconsistent_casing",
        "leading_trailing_whitespace",
        "high_null_rate",
        "mixed_numeric_and_text",
        "possible_duplicate_key",
        "numeric_outliers",
    }
)


class NumericAggregates(TypedDict):
    """Aggregates emitted ONLY for numeric *measure* columns.

    Never for string or date columns (a string min/max is a verbatim record),
    and never for identifier-like numeric columns such as ``order_id`` or a
    phone number stored as digits, where min/max would again be real values.
    The profiler enforces both rules; see profiler.is_identifier_like.
    """

    min: float
    max: float
    mean: float
    median: float
    std: float


class _ColumnProfileRequired(TypedDict):
    name: str
    dtype: str
    null_count: int
    null_pct: float
    unique_count: int
    is_numeric: bool
    issues_detected: list[str]


class ColumnProfile(_ColumnProfileRequired, total=False):
    """Aggregate description of one column. Contains NO cell values."""

    numeric: NumericAggregates


class DataProfile(TypedDict):
    """Backend -> frontend. The only object that may be forwarded to an LLM."""

    total_rows: int
    total_columns: int
    exact_duplicate_count: int  # rows that drop_exact_duplicates would remove
    profiled_at: str  # ISO-8601 UTC
    columns: list[ColumnProfile]


class ProposedAction(TypedDict):
    """Frontend -> backend. One cleaning step to preview or apply.

    ``autonomy_tier`` and ``requires_review`` are computed by
    ``src.backend.policy.classify`` from ``confidence`` and ``business_impact``.
    The backend re-derives them on apply rather than trusting the incoming
    value: the tier is a permission, and permissions are not accepted on faith.
    """

    action_id: str
    operation: str  # member of CLEANING_OPERATIONS
    target_columns: list[str]
    parameters: dict[str, object]
    confidence: float  # 0.0 - 1.0
    business_impact: str  # "low" | "high"
    rationale: str
    autonomy_tier: str
    requires_review: bool


class AuditEntry(TypedDict):
    """Backend -> frontend. One reversible state transition (CLAUDE.md 3.3)."""

    action_id: str
    timestamp: str  # ISO-8601 UTC
    operation: str
    target_columns: list[str]
    rows_before: int
    rows_after: int
    cells_changed: int
    human_decision: str
    reversible: bool
    summary: str
