# LOCAL INTEGRATION CONTRACT & ISSUE LOG

**Governed by:** `LOCAL_INTEGRATION_INSTRUCTIONS.md`
**Source of truth for schemas:** `src/contracts.py` (code) — this file is its human-readable mirror plus the issue log. If the two disagree, the code is wrong *or* this file was not updated first; either way, stop and reconcile.
**Last updated:** 2026-09-20 — integration complete. Backend (Agent 1) and frontend (Agent 2) merged into one repository; **189 tests green**; `streamlit run app.py` verified headless; **first live Claude call made and calibration checked on the demo file** (INT-002, INT-003, §6).

Read this before starting any phase. Update §1 **before** changing any key or signature. Log every cross-team bug in §2 the moment you hit it.

---

## 1. Shared Data Contracts

### A. Profiler Output Schema (Agent 1 → Agent 2) — `DataProfile`

Produced by `src.backend.profiler.profile_dataframe(df)`. **This is the only object Agent 2 may forward to Claude.**

```json
{
  "total_rows": 1250,
  "total_columns": 8,
  "exact_duplicate_count": 17,
  "profiled_at": "2026-09-20T08:15:00+00:00",
  "columns": [
    {
      "name": "order_date",
      "dtype": "String",
      "null_count": 4,
      "null_pct": 0.32,
      "unique_count": 212,
      "is_numeric": false,
      "issues_detected": ["mixed_date_formats"]
    },
    {
      "name": "amount_kes",
      "dtype": "Float64",
      "null_count": 9,
      "null_pct": 0.72,
      "unique_count": 1104,
      "is_numeric": true,
      "issues_detected": ["numeric_outliers"],
      "numeric": { "min": 120.0, "max": 984500.0, "mean": 18420.3, "median": 6850.0, "std": 41277.8 }
    }
  ]
}
```

**Rules the schema enforces — do not work around them:**

| Rule | Why |
|---|---|
| **No `sample_values`, `top_values`, `examples`, `mode`, or any cell content.** The instructions template shows `sample_values: ["…(anonymized)"]`; it is **not** supported and never will be in this schema. | "Anonymised" samples are the leak vector the whole privacy model exists to close. `tests/test_profiler.py::test_profile_contains_no_canary_values` fails the build if a value gets through on the backend; `tests/test_integration.py::test_no_canary_reaches_the_wire_end_to_end` fails it at the frontend's wire. Encode the observation as an `issues_detected` code instead. Need a code that doesn't exist? Log it in §2 and Agent 1 adds a detector. |
| `numeric` is present **only** for numeric *measure* columns. Identifier-like numerics (`order_id`, `phone`, integer columns ≥ 98 % unique) get **no** `numeric` block. When absent, the key is **omitted**, not `null`. | Their `min`/`max` are real records. Absent ≠ null across the boundary — see INT-004. |
| `issues_detected` values are drawn from the closed set `ISSUE_CODES`: `mixed_date_formats`, `inconsistent_casing`, `leading_trailing_whitespace`, `high_null_rate`, `mixed_numeric_and_text`, `possible_duplicate_key`, `numeric_outliers`. | Agent 2's tool schema and Pydantic mirror enumerate them from `src.contracts`, never retyped. |
| `profiled_at` changes every call. **Agent 2 drops it before sending to Claude.** | A changing timestamp inside the payload silently invalidates the prompt cache. Removing keys is permitted; adding them is not. |
| There is **no `file_name`** field. The UI takes it from the upload object. **It is not in the Claude payload** — filenames like `JohnSmith_arrears.xlsx` are user text. | Same residual as column names, but avoidable. |

**Field-name reconciliation against the instructions template** (template → actual):
`data_type` → `dtype` · `issues_flagged` → `issues_detected` · `file_name` → *(removed)* · `sample_values` → *(removed, by rule)* · *(added)* `null_pct`, `is_numeric`, `exact_duplicate_count`, `profiled_at`, `numeric`.

### B. Claude Execution Plan Schema (Agent 2 → Agent 1) — `list[ProposedAction]`

The backend consumes **individual actions**. The plan envelope (§1E) is Agent 2's and stays in session state; the backend never sees it. Each action:

```json
{
  "action_id": "3f9a0c2e5b7d4e1f8a6c9b0d2e4f6a8c",
  "operation": "impute_nulls",
  "target_columns": ["email"],
  "parameters": { "strategy": "mode" },
  "confidence": 0.42,
  "business_impact": "high",
  "rationale": "38% of values in this column are missing.",
  "autonomy_tier": "human_required",
  "requires_review": true
}
```

| Field | Type | Who sets it | Notes |
|---|---|---|---|
| `action_id` | str | **Agent 2, locally** (`uuid4().hex`) | Never taken from the model. `AuditLog` rejects a re-used id that is still applied. |
| `operation` | str | Model | Must be one of `CLEANING_OPERATIONS`: `drop_exact_duplicates`, `trim_whitespace`, `standardize_dates`, `impute_nulls`, `normalize_casing`, `flag_outliers`. Anything else → rejected by the strict tool schema, then by Pydantic, then by `CleaningError`. |
| `target_columns` | list[str] | Model | **A list**, not `target_column`. Empty list is legal only for `drop_exact_duplicates` (whole-row); Pydantic enforces this. |
| `parameters` | dict | Model | Per-operation, below. Unknown keys are ignored by the cleaner; the strict tool schema forbids them at the wire. |
| `confidence` | float 0–1 | Model | Input to the policy matrix. |
| `business_impact` | `"low"` / `"high"` | Model | **Input to the policy matrix — the template omits it; without it the matrix cannot run.** |
| `rationale` | str | Model | Shown to the user verbatim on the review card. |
| `autonomy_tier` | str | **Backend** (`policy.classify` via `annotate`) | Never trusted on input. Agent 2 calls `annotate()` immediately after validation and again (`effective_tier`) before every display or apply. **The strict tool schema rejects a model that tries to supply it** (`tests/test_frontend.py::test_planner_rejects_model_supplied_permission`). |
| `requires_review` | bool | **Backend** | Derived: `tier != "automatic"`. |

**`parameters` per operation:**

| operation | parameters | defaults |
|---|---|---|
| `drop_exact_duplicates` | — (`target_columns` optionally narrows the key) | whole row |
| `trim_whitespace` | — | |
| `standardize_dates` | `target_format` (strftime) | `"%Y-%m-%d"` |
| `impute_nulls` | `strategy` ∈ `median`,`mean`,`mode`,`constant`,`drop_rows`; `fill_value` (str, used by `constant`) | `median` |
| `normalize_casing` | `casing` ∈ `lower`,`upper`,`title` | `lower` |
| `flag_outliers` | `method` ∈ `iqr`,`zscore`; `threshold` (float > 0) | `iqr`, `1.5` |

**Field-name reconciliation against the instructions template** (template → actual):
`confidence_score` → `confidence` · `requires_hitl_review` → `requires_review` (derived, not model-set) · `explanation` → `rationale` · `target_column` (str) → `target_columns` (list) · `fill_nulls` → `impute_nulls` · *(added)* `business_impact`, `parameters`, `autonomy_tier` · *(added ops)* `normalize_casing`, `flag_outliers`.

### C. Audit Entry (Agent 1 → Agent 2) — `AuditEntry`

Returned by `cleaner.apply(...)` and `AuditLog.apply(...)`. The frontend never constructs one.

```json
{
  "action_id": "3f9a0c2e…",
  "timestamp": "2026-09-20T08:16:02+00:00",
  "operation": "impute_nulls",
  "target_columns": ["email"],
  "rows_before": 1250,
  "rows_after": 1250,
  "cells_changed": 476,
  "human_decision": "approved",
  "reversible": true,
  "summary": "Filled 476 missing value(s) in email using mode"
}
```

### D. Local linking — the frozen Python interface

**Do not** add `src/` to `sys.path` and import `backend.profiler`. That loads every backend module a second time under a different name (`backend.cleaner` ≠ `src.backend.cleaner`), so `Delta`, `AuditEntry`, and exception classes stop matching across the boundary. The repo is a package rooted at the project directory; `streamlit run app.py` from the root puts the root on `sys.path` and nothing else is needed. `pyproject.toml` sets `pythonpath = ["."]` for pytest.

**Backend surface the frontend imports:**

```python
from src.contracts import DataProfile, ProposedAction, AuditEntry, CLEANING_OPERATIONS, ISSUE_CODES
from src.backend.profiler import load_dataset, profile_dataframe, DatasetLoadError
from src.backend.cleaner import run, preview, apply, CleaningError
from src.backend.policy import classify, annotate, requires_review, CONFIDENCE_THRESHOLD
from src.backend.audit_logger import AuditLog, RollbackError
from src.backend.analytics import run_query, aggregate, value_counts, numeric_summary, timeseries, AnalyticsError
```

**Frontend surface (for `app.py` and tests):**

```python
from src.frontend.mcp_planner import generate_execution_plan, sanitize_profile, profile_fingerprint, PrivacyViolationError, PlannerError
from src.frontend.autonomy import effective_tier, apply_strict_mode
from src.frontend.schemas import DataProfile, PlannedAction, ProposedAction, PlannerOutput, ExecutionPlan   # Pydantic mirrors
```

| Template name | Actual call |
|---|---|
| `profile_dataset(file)` | `df = load_dataset(upload.getvalue(), filename=upload.name)` then `profile_dataframe(df)` |
| `generate_claude_plan(profile)` | `plan = generate_execution_plan(profile)` → `plan.actions_as_contract()` yields `list[ProposedAction]` dicts |
| `apply_transformations(df, plan)` | `log = AuditLog(df, source_name=…)`; per approved action: `log.apply(action, human_decision="approved")` |
| `execute_duckdb_query(df, sql)` | `run_query(log.current, sql)` — or the built helpers, which need no SQL |

Signatures:

```python
load_dataset(source: str | Path | bytes | BinaryIO, *, filename: str | None = None) -> pl.DataFrame
profile_dataframe(df: pl.DataFrame) -> DataProfile
run(df, action) -> tuple[pl.DataFrame, Delta]           # what the review card previews with; Delta has rows_before/after, cells_changed, unparsed, notes
preview(df, action) -> pl.DataFrame                      # non-mutating, no audit
apply(df, action, *, human_decision="approved") -> tuple[pl.DataFrame, AuditEntry]
classify(confidence: float, business_impact: str) -> "automatic" | "review_required" | "human_required"
annotate(action) -> action                               # copy with tier re-derived
AuditLog(original: pl.DataFrame, *, source_name="dataset")
  .apply(action, *, human_decision="approved") -> AuditEntry
  .rollback(action_id) -> pl.DataFrame     .rollback_all() -> pl.DataFrame
  .export("json" | "html") -> str          .current / .original / .entries
run_query(df, sql) -> pl.DataFrame                       # SELECT/WITH only
aggregate(df, group_by, measure, agg="sum", *, top_n=50) -> pl.DataFrame     # cols: <group_by>, value
value_counts(df, column, *, top_n=20) -> pl.DataFrame                        # cols: value, count  (local screen only)
numeric_summary(df, column) -> pl.DataFrame
timeseries(df, date_column, measure, *, grain="month", agg="sum") -> pl.DataFrame   # cols: period, value
generate_execution_plan(profile, *, client=None, max_attempts=2) -> ExecutionPlan   # client injectable for tests
sanitize_profile(profile) -> dict                        # raises PrivacyViolationError before any I/O
effective_tier(action, *, strict: bool) -> str           # annotate() then Strict-mode demotion
```

Exceptions to catch: `DatasetLoadError`, `CleaningError`, `AnalyticsError`, `RollbackError`, `PrivacyViolationError`, `PlannerError`, and `ValueError` from `classify` on malformed input.

Reference dataset for both sides: `tests/fixtures/messy_sales.csv` — 24 rows, all seven issue codes fire. Demo dataset for the UI: `data/samples/messy_sales.csv` — 50 rows, six codes fire (INT-014).

### E. Plan envelope (Agent 2 only) — `ExecutionPlan`

Frontend session state; the backend never receives it.

```json
{
  "plan_id": "uuid4 hex, minted locally",
  "dataset_summary": "Plain-language, aggregate-only description for the user.",
  "actions": ["… list[ProposedAction] as in §1B …"],
  "analysis_questions": ["Up to five business questions; Insights tab lists them"]
}
```

What Claude actually emits is `PlannerOutput` = `dataset_summary` + `actions: list[PlannedAction]` + `analysis_questions`, where a `PlannedAction` is a `ProposedAction` **without** `action_id`, `autonomy_tier`, `requires_review`. Those three are added locally. The tool schema is `strict: True` / `additionalProperties: False`, and its `operation` enum is `sorted(CLEANING_OPERATIONS)` so the cached prompt prefix is byte-stable.

**Autonomy matrix** (implemented once, in `src.backend.policy`; threshold **inclusive**, `>= 0.90`):

| | low impact | high impact |
|---|---|---|
| confidence ≥ 0.90 | `automatic` | `review_required` |
| confidence < 0.90 | `review_required` | `human_required` |

`human_required` is **blocked** — not queued, not approvable in one click. A single boolean cannot express this, which is why `business_impact` exists. The frontend's Strict toggle demotes `automatic` → `review_required` and nothing else (`src.frontend.autonomy`). In Semi mode, `automatic` actions are applied through `AuditLog.apply` the moment the plan arrives and appear in the Audit Trail with an undo.

---

## 2. Active Issues & Edge Cases Register

### Backend phase (Agent 1)

| ID | Originating | Description | Status | Resolution / Target |
|---|---|---|---|---|
| ISS-001 | Agent 1 | **DuckDB access violation (`0xC0000005`) when a Polars frame is registered directly** — duckdb 1.5.5 / polars 1.44, frames with nulls in string columns. Also produced *silently wrong* results in one run. | **RESOLVED** | `analytics._connection` registers `df.to_arrow(compat_level=oldest)`; `pyarrow` is a hard requirement; regression test in place. Confirmed stable on the integration machine (Python 3.11.7). **Never call `duckdb.register` on a frame yourself — go through `src.backend.analytics`.** Upstream report still to file. |
| ISS-002 | Agent 1 | Schema divergence between the instructions template and the built contract (`sample_values`, `target_column`, `confidence_score`, `fill_nulls`, missing `business_impact`). | **RESOLVED 2026-09-20** | Frontend adopted §1 names wholesale: Pydantic mirrors in `src/frontend/schemas.py` import the vocabularies from `src.contracts`, and `test_pydantic_mirrors_typeddict_contracts` fails the build on any field-set drift. |
| ISS-003 | Agent 1 | `run_query` keyword denylist (`\bset\b`, `\bload\b`, `\bdelete\b`, …) will reject a valid SELECT that references a column with one of those names. | OPEN — low | Swap to a DuckDB `EXPLAIN`-based statement-type check if a real header hits it. The UI does not expose free SQL in Phase 1, so no user can hit it yet. |
| ISS-004 | Agent 1 | `AuditLog` keeps one before-snapshot per applied step. Fine at spreadsheet scale; unbounded for very large frames × many steps. | OPEN — monitor | Cap or spill snapshots to disk if a real workload shows pressure. |
| ISS-005 | Agent 1 | Identifier rule (`is_identifier_like`) is name-pattern + uniqueness heuristics, tuned on synthetic headers only. | OPEN | Review against real SME column names in the first joint session. |
| ISS-006 | Agent 1 | `standardize_dates` keeps unparseable values verbatim; the UI should surface `Delta.unparsed` so the user knows. | **RESOLVED 2026-09-20** | Review card renders every `Delta.notes` line ("N value(s) did not match any known date format and were left unchanged"). |
| ISS-007 | Agent 2 (template) | Streamlit re-renders can trigger duplicate Claude API calls. | **RESOLVED 2026-09-20** | The call sits behind an `st.button` and is guarded by `profile_fingerprint()` — a sha256 of the *sanitized* payload, so it is stable across `profiled_at` changes. A plan for the same fingerprint is never regenerated. |
| ISS-008 | Agent 1 (template) | Large CSV memory when parsing: `read_csv` loads the whole file. | OPEN — Phase 2 | `scan_csv` + streaming for profiling. |
| ISS-009 | Agent 1 | xlsx ingestion tested only via write→read round-trip; real workbooks with merged headers / multiple sheets / title rows untested. | OPEN — Phase 2 | Backlog. `test_load_xlsx_roundtrip` now runs (it was skipped in Agent 1's environment for want of `xlsxwriter`). |

### Integration phase (2026-09-20)

| ID | Originating | Description | Status | Resolution / Target |
|---|---|---|---|---|
| INT-001 | Integration | Agent 1's code arrived as a nested folder `DATAYANGU INTELLIGENCE/` containing a byte-identical copy of itself, each with a venv hardwired to `C:\Users\lenovo\…\python3.14` — 96,800 unusable files. | **RESOLVED** | Byte-identity verified with `diff -r`, then both copies' venvs and the inner duplicate deleted; unique files moved to the root; folder removed. CRLF line endings on the migrated files normalised to LF. |
| INT-002 | Integration | No live Claude call had been made; all planner tests use a stub. | **RESOLVED 2026-09-20** | First live call made through the app's own code path (`generate_execution_plan(profile)` with the zero-arg client): auth OK against `claude-opus-5`, 6-action plan returned in 31.4 s. Keys on the wire: `total_rows`, `total_columns`, `exact_duplicate_count`, `columns` — aggregates only. Stub-based tests remain the CI path. |
| INT-003 | Integration | Confidence calibration unverified. The autonomy gate rests on the model not rating financial imputation as safe. | **VERIFIED (n=1) 2026-09-20** | The model **declined to propose imputing `revenue` at all**, stating in its summary that inventing numbers in a financial column would silently change sales totals. It rated `drop_exact_duplicates` **high / 0.68 → human_required** (repeated invoice ids might be genuine repeat billing), `impute_nulls(region, constant "Unknown")` low / 0.84 → review_required, and the four format-only actions automatic (0.92–0.99). Matches intent. **One run on one synthetic file** — re-check on 2–3 real customer files before relying on Semi mode. |
| INT-004 | Integration | **Absent ≠ null across the boundary.** The TypedDict omits `numeric` for non-measure columns; the Pydantic mirror's `Optional[NumericAggregates] = None` serialised it as `null`, which `sanitize_profile` rejected as a malformed aggregates block. | **RESOLVED** | `model_dump(exclude_none=True)` on the Pydantic path; sanitizer skips `numeric: None`. Rule recorded in §1A. |
| INT-005 | Integration | `streamlit.testing.v1.AppTest.from_file()` resolves relative paths against the *calling test file*, not CWD. | **RESOLVED** | `tests/test_integration.py` passes an absolute path. |
| INT-006 | Integration | Two competing `PHASE_1_IMPLEMENTATION.md` files (backend v2.0 claimed to supersede the frontend v1.0). Both scopes are real. | **RESOLVED** | Renamed to `docs/implementations/PHASE_1_BACKEND.md` and `PHASE_1_FRONTEND.md`; headers cross-reference; all links relinked; frontend spec rewritten as v1.1 reconciled to `src/contracts.py`. |
| INT-007 | Integration | `fastmcp` installed, unused in Phase 1, pulls ~25 transitive packages (`mcp`, `starlette`, `uvicorn`, `authlib`, `cryptography`, `keyring`). | OPEN — user decision | Kept in `requirements.txt` per the stack docs; drop unless Phase 2 builds an MCP server. |
| INT-008 | Integration | `pandas` 3.0.6 installed transitively via Streamlit, contrary to `CLAUDE.md` §2. | ACCEPTED | Unavoidable. `test_boundaries.py` enforces that no backend file imports it; no frontend file does either. |
| INT-009 | Integration | Repo lives in a OneDrive-synced folder. First `pip install` exceeded 10 minutes (sync contention on ~48k venv files). | OPEN | Relocate outside OneDrive if tooling stays sluggish. Post-install performance is fine (§6). |
| INT-010 | Integration | Not a git repository. ~4,000 lines across both codebases are untracked and unbacked. | OPEN — user decision | User chose to leave git alone. `CLAUDE.md` §3.2 branch rules cannot apply until `git init`. |
| INT-011 | Integration | Frontend spec v1.0 said the confidence threshold is exclusive (`> 0.90`); backend implements inclusive (`>= 0.90`). | **RESOLVED** | Backend wins — it is implemented and tested (`test_policy.py` covers both sides of 0.90). Frontend deleted its own `policy.py`; `review_card` badges use `CONFIDENCE_THRESHOLD` from the backend. Docs updated. |
| INT-012 | Integration | Insights tab charts heuristic defaults (first low-cardinality text column × first numeric measure; monthly trend on the standardised date column). Claude's `analysis_questions` are listed, not executed. | OPEN — Phase 2 | NL→SQL over `run_query` with the same read-only guards. Not a bug; documented in the UI caption. |
| INT-013 | Integration | `src/contracts.py` docstring pointed at the pre-rename spec path. | **RESOLVED** | Doc-only edit to a backend-owned file during integration; no contract change. |
| INT-014 | Integration | The 50-row demo file fires 6 of 7 issue codes (`mixed_numeric_and_text` does not: only 1 of 49 `invoice_id` values is numeric-like, under the 20 % floor). | ACCEPTED | It is a demo, not the reference; `tests/fixtures/messy_sales.csv` fires 7/7 and `test_demo_sample_flows_through_backend` pins the six. |
| INT-015 | Integration | **`.env` was never loaded.** Streamlit does not read `.env` and neither did the app, so a correctly configured key produced `TypeError: Could not resolve authentication method` as a raw traceback in the UI. | **RESOLVED 2026-09-20** | `_build_client` loads `.env` from an explicit repo-root path (python-dotenv's default search walks the *caller's* stack frame and behaves differently under `streamlit run`, pytest and `python -`), tolerating a Notepad BOM; every SDK failure is translated to one `PlannerError` with plain wording (missing credentials / 401 / 403 / 404 / 429 / connection); `python-dotenv` is now an explicit requirement; two tests added. |
| INT-016 | Integration | **`StreamlitDuplicateElementId` on `download_button`** once both the Review Queue and Audit Trail tabs had data: `_render_downloads` is called from both with identical parameters, so the auto-generated element ids collided. The headless boot test could not see it - with nothing loaded, both tabs return before rendering downloads. | **RESOLVED 2026-09-20** | Explicit `key=f"{prefix}_download_*"` per call site; the two Insights charts keyed for the same reason. New `test_streamlit_app_renders_populated_state` seeds session state with a real profile, plan and audit log so every tab renders (3 Approve buttons in Strict mode, 1 Undo) - the class of bug an empty boot cannot catch. |

---

## 3. What to Look Out For (Integration Risks)

- **Import identity.** One import root only: `src.backend.*` / `src.frontend.*` / `src.contracts`. A second path (`backend.*`) means two copies of every class and `isinstance` checks that silently fail. See §1D.
- **PII leakage lives entirely on Agent 2's side now.** The backend cannot emit a cell value; Agent 2 can still *add* one. Nothing from `run()`, `preview()`, `value_counts()`, `AuditLog.current`, or the raw frame may enter a Claude payload. Only `profile_dataframe()` output, keys removed, never keys added. Column **names** remain the one user-authored residual — disclose before the first call; pseudonymisation is backlog.
- **Absent is not null.** TypedDicts omit optional keys; Pydantic `Optional` fields serialise `None`. Any new optional field crossing the boundary must be dumped with `exclude_none=True` and tolerated as missing on receipt (INT-004).
- **`annotate()` on every action, every time.** The tier the model (or a stale session) carries is not a permission. `effective_tier()` re-derives before every display and every apply.
- **dtypes change after cleaning.** `standardize_dates` returns **String** columns in ISO format, not Date. `flag_outliers` adds a Boolean `<col>_outlier_flag` column. Charts and selectors keyed on the original schema must re-read `log.current.schema`; the review card's "after" slice includes new flag columns for this reason.
- **DuckDB registration.** Always via `src.backend.analytics`; never `duckdb.register(name, polars_df)` directly (ISS-001). A String date column must be `TRY_CAST` (the built `timeseries` does this).
- **`profiled_at` and prompt caching.** Dropped from the payload by `sanitize_profile`; `profile_fingerprint` is computed on the sanitized payload so it is stable too.
- **`action_id` re-use.** `AuditLog.apply` raises if an id is still applied. After `rollback`, the id is free again; `app.py` resets `applied`/`decisions` for undone ids so the card re-offers them.
- **Two separate outlier thresholds by design.** Detection (`profiler`) uses 3×IQR so it stays quiet; the `flag_outliers` action defaults to 1.5×IQR because a human asked for it. Don't "fix" one to match the other.
- **Streamlit reruns.** Every interaction re-executes `app.py`. State is declared once in `_init_state()`; the Claude call is behind a button *and* a fingerprint guard; same-bytes re-uploads are a no-op via `upload_digest`.

---

## 4. Backlog: Future Enhancements & Additions

- [x] ~~First live Claude run and calibration check~~ (INT-002, INT-003) — done 2026-09-20, see §6.
- [ ] Re-check calibration on 2–3 real customer spreadsheets before relying on Semi mode (n=1 so far).
- [ ] Log `response.usage` per plan call for cost tracking; consider `output_config.effort` to trade the ~30 s round trip against plan quality.
- [ ] NL→SQL for `analysis_questions` over `run_query` with the existing read-only guards (INT-012).
- [ ] Multi-sheet Excel workbooks and header-row detection (ISS-009).
- [ ] Local rule memory in SQLite to persist approved HITL decisions per organisation (proposal §5.6).
- [ ] PDF Quality Assessment report. *(HTML change log exists: `AuditLog.export("html")`, downloadable from the UI.)*
- [ ] Streaming/chunked CSV profiling for large files (ISS-008).
- [ ] Opt-in column-name pseudonymisation before the Claude payload (closes the last residual).
- [ ] Synthetic format-preserving placeholders (`"Xxxxx Xxxx"`, `"00/00/0000"`) if format inference proves weak — the privacy-safe answer to the template's `sample_values`.
- [ ] `EXPLAIN`-based read-only check for `run_query` (ISS-003).
- [ ] Upstream bug report to DuckDB with the 24-row repro (ISS-001).
- [ ] Additional issue codes on request (e.g. `currency_symbol_in_numeric`, `phone_format_mixed`) — request here, do not send samples.
- [ ] `AuditLog` snapshot spill-to-disk (ISS-004).
- [ ] Decide `fastmcp` (INT-007); relocate out of OneDrive (INT-009); `git init` (INT-010).
- [x] ~~Rollback UI for individual applied steps~~ — Audit Trail tab, "Undo from here".
- [x] ~~Side-by-side before/after preview on review cards~~ — via `cleaner.run`, local only.

---

## 5. Change Log (this file)

| Date | Agent | Change |
|---|---|---|
| 2026-09-20 | Agent 2 | Initial frontend-side version with the v1.0 frontend schemas and a divergence table against the instructions template. Superseded below. |
| 2026-09-20 | Agent 1 | Rewritten from the built backend (138 tests). §1 schemas from `src/contracts.py`; reconciled against the template; ISS-001…009. |
| 2026-09-20 | Integration | Merged both versions on Agent 1's structure. Frontend adopted §1 wholesale (ISS-002 closed). Added §1E envelope, frontend import surface, INT-001…014, §6 record. Closed ISS-006, ISS-007. |
| 2026-09-20 | Integration | First live Claude call (INT-002 closed, INT-003 verified n=1). `.env` loading bug fixed (INT-015). Tests 186 → 188. |
| 2026-09-20 | Integration | Duplicate download-button ids on populated tabs (INT-016); populated-state AppTest added. Tests 188 → 189. |

---

## 6. Integration Record — 2026-09-20

**Merged.** Agent 1's `src/backend/*`, `src/contracts.py`, six test files and the 24-row fixture moved from the nested `DATAYANGU INTELLIGENCE/` folder to the repo root. Agent 2's frontend rewritten to the backend contract (schemas, prompts, planner, card, app; frontend `policy.py` deleted in favour of `src.backend.policy`). Shared config adopted from Agent 1 (`pyproject.toml`, `requirements.txt` with `pyarrow` + `xlsxwriter`, `.gitignore` with a `data/samples/*.csv` exception, Anthropic-only `CLAUDE.md`). Dead files removed: the nested duplicate, two foreign venvs, the byte-identical `(1)` proposal, the truncated `gemini-code-*` prefix, the 71-line `Agent1.md` (superseded by `docs/AGENT_1_BACKEND.md`), the static JSON profile fixture (replaced by the live profiler in `conftest.py`).

**Tests.** `pytest tests/ -q` → **188 passed, 0 failed, 0 skipped** — 14.2 s / 12.7 s / 10.7 s across three consecutive full runs at 186, then 17.4 s / 16.1 s at 188 after the two SDK-error-translation tests (INT-015). By file: profiler 28 · cleaner 40 · policy 22 · audit_logger 14 · analytics 25 · boundaries 9 · frontend 44 · integration 6. Two failures surfaced and were fixed during the merge (INT-004, INT-005). Agent 1's suite, previously 137 + 1 skip on their machine, is 138 green here.

**End-to-end.** `test_full_pipeline_end_to_end`: 24 rows → all 7 issue codes → stubbed plan of 5 actions → tiers `automatic ×4`, `human_required ×1` (the financial imputation) → 4 applied via `AuditLog` → 21 rows, whitespace trimmed, dates ISO, regions title-cased, the blocked imputation **did not run** → 4 audit entries and 4 before/after log lines → DuckDB aggregate + monthly timeseries → Plotly figures → JSON/HTML export → `rollback_all()` byte-equal to the original.

**Privacy.** `test_no_canary_reaches_the_wire_end_to_end`: a frame whose every column *is* a canary (card number as integers, email, phone, marker) → the exact string handed to the client contains none of them; the identifier column has no aggregates, the measure column has exactly five.

**Launch.** `launch.sh --no-test` headless: Streamlit up, `/_stcore/health` → `200 ok`, `/` → 200, **9.8 s** including the pip check, port released after `taskkill /T`. `launch.bat --test` from PowerShell: dependencies satisfied, **186 passed in 12.7 s, exit code 0**. `AppTest.from_file("app.py").run()`: no exception, four tabs, `autonomy_mode == "strict"`, `plan is None`.

**Live call (INT-002).** First real Claude call, 2026-09-20, via `generate_execution_plan(profile)` on the 50-row demo file — the app's exact code path, with `.env` loaded by `_build_client` (proven: `ANTHROPIC_API_KEY` absent from the process before the call, present after). Auth OK against `claude-opus-5`. Six actions in 31.4 s: `trim_whitespace` 0.99, `normalize_casing(title)` 0.92, `standardize_dates(%Y-%m-%d)` 0.94, `flag_outliers(iqr, 1.5)` 0.95 — all low impact → automatic; `impute_nulls(region, constant "Unknown")` 0.84 low → review_required; `drop_exact_duplicates` **0.68 high → human_required**, with the rationale that repeated invoice ids may be genuine repeat billing. The model proposed **no** imputation of `revenue` or `units_sold` and said why. Five analysis questions returned. Strict tool schema, Pydantic validation, local id minting and backend tier derivation all held on real output.

**Performance observations.** Post-install everything is fast: suite 14 s, app healthy in <10 s, demo profile <1 s. The one slow thing at install time was `pip install` in OneDrive (>10 min, INT-009). At run time the plan call is the long pole: ~31 s with adaptive thinking on Opus 5 — the UI shows a spinner; `output_config.effort` is the lever if that must come down (backlog). Agent 1's DuckDB workaround (Arrow registration) held on Python 3.11.7 / duckdb 1.5.5 / polars 1.44.2 / pyarrow 25.0.1 — 25 analytics tests including the 10× stability regression.

**Warnings.** (1) The live-call evidence is one run on one synthetic file (INT-003, n=1); re-check calibration on real customer data before relying on Semi mode. (2) Column names still travel to Claude. (3) The `.gitignore` ignores `*.csv` globally with explicit exceptions for `tests/fixtures/` and `data/samples/` — any new sample must be added to the exception or it silently won't be tracked, once git exists.
