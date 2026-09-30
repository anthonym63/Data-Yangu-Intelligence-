# AGENT 1: Backend Data Engine & Deterministic Execution Lead

*Give this file to **Developer / AI Agent 1**. This agent is strictly responsible for local
data ingestion, profiling, deterministic cleaning, DuckDB analytics, the autonomy policy
matrix, and the reversible audit log.*

> **Status:** This is the active brief — the backend is built by this team; the frontend
> by the other team. The binding technical contract is
> [`implementations/PHASE_1_BACKEND.md`](implementations/PHASE_1_BACKEND.md) (v2.0);
> this document is the scope boundary, not the specification. Phase 1 implemented 2026-09-20.

---

## Role & Scope

You are **Agent 1**. You own everything that touches raw data. **No code you write may ever
make a network call.** The entire backend runs locally, which is what makes the platform's
privacy claim true rather than aspirational.

**Strict file ownership:**

- `src/contracts.py` (drafted here, frozen jointly with the frontend team)
- `src/backend/profiler.py`
- `src/backend/cleaner.py`
- `src/backend/policy.py`
- `src/backend/analytics.py`
- `src/backend/audit_logger.py`
- `tests/conftest.py`, `tests/test_profiler.py`, `tests/test_cleaner.py`, `tests/test_policy.py`,
  `tests/test_audit_logger.py`, `tests/test_analytics.py`, `tests/test_boundaries.py`,
  `tests/fixtures/messy_sales.csv`

**Do NOT modify** `app.py`, anything under `src/frontend/`, or `tests/test_frontend.py` —
those belong to the frontend team.

**`src/contracts.py` is the handshake.** Every field is a wire format between the two
teams; change it only by agreement. `policy.classify` is the single autonomy matrix — the
frontend imports it and must not reimplement it.

---

## The one rule that outranks the others

`src/backend/profiler.py` is where a privacy breach would originate. A profile is the only
thing that leaves the local runtime, so **a profile must never contain a cell value.**

Permitted in a `ColumnProfile`: name, dtype, counts, percentages, and numeric aggregates
(`min`, `max`, `mean`, `median`, `std`) on genuinely numeric columns.

**Prohibited, without exception:** sample rows, head/tail previews, top-K category value
lists, modal values, example "before/after" strings, and `min`/`max` on string or date
columns — a string min/max *is* a customer record. If you find yourself wanting to send an
example so Claude can "see the format", encode the observation as a flag in
`issues_detected` (e.g. `"mixed_date_formats"`) instead of the value that demonstrates it.

Before/after examples still get shown to the **human** in the HITL review queue. They travel
from your local frame to the Streamlit UI directly. They never enter an API payload.

---

## Phase-by-Phase Development Instructions

### Phase 1: Ingestion & Profiling

Build `profiler.py`:

- `load_dataset(source: io.BytesIO) -> pl.DataFrame` — Polars readers for `.csv` and `.xlsx`
  (`read_excel` needs `openpyxl`). `app.py` passes a `BytesIO` of the upload. Infer schema
  permissively; a messy file must not raise.
- `profile_dataframe(df: pl.DataFrame) -> DataProfile` — returns the `DataProfile` TypedDict
  from `src/contracts.py`. Per column: dtype, `null_count`, `null_pct`, `unique_count`,
  `is_numeric`, numeric aggregates where applicable, and `issues_detected`.
- Detectors feeding `issues_detected`: `mixed_date_formats`, `inconsistent_casing`,
  `leading_trailing_whitespace`, `high_null_rate`, `mixed_numeric_and_text`,
  `possible_duplicate_key`, `numeric_outliers`.

**Test:** `pytest tests/test_profiler.py -v`. Include a test asserting that
`json.dumps(profile)` contains none of the canary values seeded into the test frame.

### Phase 2: Deterministic Cleaning & Policy

Build `cleaner.py` — one pure function per operation, each taking and returning a
`pl.DataFrame` plus a row/cell delta summary. No function may consult an LLM:
`drop_exact_duplicates`, `trim_whitespace`, `standardize_dates`, `impute_nulls`,
`normalize_casing`, `flag_outliers`.

Build `policy.py` — `classify(confidence, business_impact) -> AutonomyTier` per the matrix
in [`DEVELOPMENT_PROPOSAL_HYBRID_AUTONOMY.md`](DEVELOPMENT_PROPOSAL_HYBRID_AUTONOMY.md) §5.2,
plus `annotate(action)` which re-derives the tier on an incoming `ProposedAction`. High
confidence plus high impact is **still** `review_required`.

**The interface the frontend calls — these signatures are frozen:**

```python
def apply(df: pl.DataFrame, action: ProposedAction) -> tuple[pl.DataFrame, AuditEntry]: ...
def preview(df: pl.DataFrame, action: ProposedAction) -> pl.DataFrame: ...   # non-mutating
```

`apply` dispatches on `action["operation"]`, returns the new frame **and** the `AuditEntry`
for that step (you own the audit log — CLAUDE.md §3.3; the frontend never constructs one).
`preview` produces the "after" frame for the review card without touching state. The full
frozen import list is in the spec, §4.

**Test:** `pytest tests/test_cleaner.py -v`. Every cleaner needs an idempotency test:
applying it twice must equal applying it once.

### Phase 3: Analytics & Reversible Audit Log

Build `analytics.py` — a DuckDB runner over the cleaned Polars frame, returning aggregated
result tables for charting. Never interpolate user or model strings into SQL.

Build `audit_logger.py` — per CLAUDE.md §3.3, **every transformation logs a before/after
state transition**. Each `AuditEntry` records `action_id`, timestamp, operation, rows before
and after, the human decision, and enough state to reverse the step. Expose
`rollback(action_id)` and `export(format: "json" | "html")`.

**Test:** `pytest tests/test_audit_logger.py -v`. The load-bearing test: apply a chain of
transformations, roll back to step 0, and assert frame equality with the original.

---

## Handshake

You produce `DataProfile` and `AuditEntry`. You consume `ProposedAction[]`. All three are
defined in `src/contracts.py`. The frontend team can run `profile_dataframe` on
`tests/fixtures/messy_sales.csv` to see a realistic profile with all seven issue codes
firing — that fixture is the shared reference dataset.

Before any commit: `pytest tests/ -v` with zero failures (CLAUDE.md §3.2).
