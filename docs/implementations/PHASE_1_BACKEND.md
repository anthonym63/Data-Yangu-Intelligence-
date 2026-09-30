# PHASE 1 IMPLEMENTATION SPECIFICATION — BACKEND DATA ENGINE
## AI Data Operations & Analyst Platform for SMEs

**Version:** 2.0 — backend engine. Frontend counterpart: [`PHASE_1_FRONTEND.md`](PHASE_1_FRONTEND.md)
**Status:** Implemented 2026-09-20. See §7 for the record and open items.
**Owning team:** Backend / Agent 1
**Consumer:** Frontend team (Streamlit UI, Claude planner, HITL queue) — see §4 for the exact interface you call

---

## 0. Purpose & invariants

This document is the contract between the backend engine and everything that uses it. It
resolves what the proposal set left implicit: exact schemas, exact function signatures,
the algorithm behind every detector and every cleaning operation, and the tests that prove
the privacy claim.

Three invariants, stated once:

> **1. The backend never touches the network.** No module under `src/backend/` imports
> anything that can open a socket. This is enforced by `tests/test_boundaries.py`, which
> parses every backend file's imports. The privacy model depends on the data path being
> local; a convention would not be enough.

> **2. A `DataProfile` contains no cell value.** It is the only object the frontend may
> forward to an LLM. Detectors report *that* a pattern exists, never *which* value shows
> it. Numeric `min`/`max` are withheld for identifier-like columns, because the min of an
> `order_id` column is a real order. Enforced by the canary tests in `tests/test_profiler.py`.

> **3. Every transformation is reversible and logged.** `cleaner.apply` emits an
> `AuditEntry` and a before/after log line; `AuditLog` keeps a before-snapshot per step
> and can roll back to any point. Enforced by `tests/test_audit_logger.py`, whose
> load-bearing test is "apply a chain, roll back to step 0, assert byte-equality with the
> original".

---

## 1. Ownership

| Path | Owner | Notes |
|---|---|---|
| `src/contracts.py` | **Backend drafts, both teams freeze** | Wire formats. Dependency-free. Change by agreement only. |
| `src/backend/profiler.py` | Backend | Ingestion + profiling + issue detection |
| `src/backend/cleaner.py` | Backend | Six deterministic operations + `run`/`preview`/`apply` |
| `src/backend/policy.py` | Backend | The autonomy matrix. Frontend imports it; nothing reimplements it |
| `src/backend/audit_logger.py` | Backend | `AuditLog` (snapshots, rollback, export) + stateless helpers |
| `src/backend/analytics.py` | Backend | Read-only DuckDB over a Polars frame |
| `tests/test_{profiler,cleaner,policy,audit_logger,analytics,boundaries}.py`, `tests/conftest.py`, `tests/fixtures/messy_sales.csv` | Backend | |
| `app.py`, `src/frontend/**`, `tests/test_frontend.py` | **Frontend team** | Not touched by the backend |

`requirements.txt`, `.gitignore`, `pyproject.toml`, `CLAUDE.md` are repo-level and shared.

---

## 2. Schemas — `src/contracts.py`

`TypedDict`s, not Pydantic: these cross a team boundary as plain JSON, and the module must
stay importable with zero dependencies. Python 3.10 floor (the optional `numeric` key is
expressed via `total=False` inheritance, not `NotRequired`).

| Type | Direction | Purpose |
|---|---|---|
| `DataProfile` | backend → frontend | `total_rows`, `total_columns`, `exact_duplicate_count`, `profiled_at`, `columns: list[ColumnProfile]` |
| `ColumnProfile` | backend → frontend | `name`, `dtype`, `null_count`, `null_pct`, `unique_count`, `is_numeric`, `issues_detected`, optional `numeric` |
| `NumericAggregates` | backend → frontend | `min`, `max`, `mean`, `median`, `std` — **measure columns only** |
| `ProposedAction` | frontend → backend | `action_id`, `operation`, `target_columns`, `parameters`, `confidence`, `business_impact`, `rationale`, `autonomy_tier`, `requires_review` |
| `AuditEntry` | backend → frontend | `action_id`, `timestamp`, `operation`, `target_columns`, `rows_before`, `rows_after`, `cells_changed`, `human_decision`, `reversible`, `summary` |

Closed vocabularies, exported as both `Literal` types and runtime `frozenset`s:

- `CLEANING_OPERATIONS` = `drop_exact_duplicates`, `trim_whitespace`, `standardize_dates`, `impute_nulls`, `normalize_casing`, `flag_outliers`
- `ISSUE_CODES` = `mixed_date_formats`, `inconsistent_casing`, `leading_trailing_whitespace`, `high_null_rate`, `mixed_numeric_and_text`, `possible_duplicate_key`, `numeric_outliers`
- `AutonomyTier` = `automatic` | `review_required` | `human_required`
- `BusinessImpact` = `low` | `high`
- `HumanDecision` = `pending` | `approved` | `rejected` | `edited`

**`autonomy_tier` is never trusted on input.** `policy.annotate(action)` re-derives it from
the action's own `confidence` × `business_impact`. A permission is not accepted on faith.

---

## 3. Module specifications

### 3.1 `profiler.py`

**`load_dataset(source, *, filename=None) -> pl.DataFrame`**
`source` is a path, raw `bytes`, or a binary file-like (a Streamlit upload's buffer);
`filename` supplies the extension when `source` isn't a path. `.csv` via
`pl.read_csv(infer_schema_length=10_000, ignore_errors=True, try_parse_dates=False,
truncate_ragged_lines=True)` — dates are deliberately left as strings so the detector can
see mixed formats. `.xlsx` via `pl.read_excel(engine="openpyxl")`. Any reader failure is
wrapped in `DatasetLoadError`. Messy *content* never raises; only unreadable *structure* does.

**`profile_dataframe(df) -> DataProfile`**
`exact_duplicate_count = height − unique(df).height` — the number of rows
`drop_exact_duplicates` would remove, not the number of rows in duplicate groups. Per
column: dtype string, null count/pct (rounded 2), `unique_count` excluding nulls,
`is_numeric = dtype.is_numeric()`, aggregates (below), issue codes (below).

**Identifier rule — `is_identifier_like(name, series, *, unique_count, non_null)`**
True if the name matches an identifier/sensitive pattern (`id`, `key`, `code`, `no`,
`number`, `ref`, `phone`, `mobile`, `card`, `pan`, `account`, `iban`, `ssn`, `pin`,
`passport`, `plate`, … as whole tokens), **or** it is an integer column with
`unique/non_null ≥ 0.98` over ≥ 10 rows. Identifier-like columns get **no** `numeric`
block and are eligible for `possible_duplicate_key`.

**Numeric aggregates** — emitted only when `is_numeric and non_null > 0 and not
identifier_like`. `std` of a single value is `0.0`, not an error.

**Detectors.** All string-column detectors run in one lazy Polars `select`; each returns a
count or boolean. No value is materialised into Python.

| Code | Applies to | Rule |
|---|---|---|
| `high_null_rate` | any | `null_pct ≥ 20.0` |
| `possible_duplicate_key` | identifier-like | `unique_count < non_null` |
| `numeric_outliers` | numeric measures, ≥ 8 non-null | any value outside `[q1 − 3·IQR, q3 + 3·IQR]`; skipped when IQR = 0 |
| `leading_trailing_whitespace` | string | `count(col != strip(col)) > 0` |
| `inconsistent_casing` | string | `n_unique(lower(strip(col))) < n_unique(strip(col))` and `n_unique > 1` |
| `mixed_numeric_and_text` | string, ≥ 5 non-null | `0.20 ≤ fraction castable to Float64 < 1.0` |
| `mixed_date_formats` | string | ≥ 2 of seven anchored date shapes match ≥ 1 value each, and total date-shaped coverage ≥ 50 % |

Tunables are module constants (`HIGH_NULL_THRESHOLD_PCT`, `OUTLIER_IQR_MULTIPLIER`, …).

### 3.2 `cleaner.py`

Each operation is `(df, …) -> (new_df, Delta)` where `Delta` carries `rows_before`,
`rows_after`, `cells_changed`, `unparsed`, `notes`. Guarantees: pure, idempotent (tested
per operation), never turns a value into a null because it failed to understand it.

| Operation | Parameters | Behaviour |
|---|---|---|
| `drop_exact_duplicates` | `target_columns` as optional subset | `df.unique(keep="first", maintain_order=True)` |
| `trim_whitespace` | — | `str.strip_chars()`; text columns only |
| `normalize_casing` | `casing: lower / upper / title` | text columns only |
| `standardize_dates` | `target_format` (default `%Y-%m-%d`) | Try 16 formats (`DATE_FORMATS`, day-first before month-first), `coalesce`, re-emit as string in target format. **Unparseable values are kept verbatim** and counted in `Delta.unparsed`. Date/Datetime dtypes are formatted directly. |
| `impute_nulls` | `strategy: median / mean / mode / constant / drop_rows`, `fill_value` | `median`/`mean` require numeric; `mode` sorts candidates for determinism; `constant` casts `fill_value` to the column dtype and raises if it doesn't fit; `drop_rows` drops rows with a null in any target |
| `flag_outliers` | `method: iqr / zscore`, `threshold` | Adds `<col>_outlier_flag: Boolean` (nulls → `False`). **Changes no existing value.** |

**Dispatch.** `run(df, action)` validates `operation ∈ CLEANING_OPERATIONS`, pulls
`target_columns`/`parameters`, and calls the operation. `preview(df, action)` returns the
frame only. `apply(df, action, *, human_decision="approved")` returns
`(frame, AuditEntry)` and emits the before/after log line. A module-level assertion keeps
the dispatch table equal to `CLEANING_OPERATIONS`. Everything off-contract raises
`CleaningError` (a `ValueError`).

### 3.3 `policy.py`

```
                       low impact         high impact
confidence >= 0.90     automatic          review_required
confidence <  0.90     review_required    human_required
```

`classify(confidence, business_impact)` raises `ValueError` on out-of-range confidence,
booleans, or unknown impact — a malformed proposal must never default to `automatic`.
`annotate(action)` returns a copy with `autonomy_tier`/`requires_review` re-derived.
`CONFIDENCE_THRESHOLD = 0.90` is the single tunable.

### 3.4 `audit_logger.py`

Stateless: `make_entry(action, delta, *, human_decision)` and `log_transition(entry)`
(the INFO line `audit | <op> | rows a -> b | cells_changed=n | decision=… | action_id=…`).

Stateful `AuditLog(original, *, source_name)`:
- `apply(action, *, human_decision)` — runs against `current`, stores the before-snapshot
  keyed by `action_id`, appends the entry. Re-using an `action_id` still in the log raises.
- `record(entry, before, after)` — register a step applied via `cleaner.apply` elsewhere.
- `rollback(action_id)` — restores the snapshot taken *before* that step, removes it and
  everything after it from the active log, logs the undo. `rollback_all()`.
- `export("json" | "html")` — HTML is escaped; JSON is `steps` plus totals.

Snapshots are whole frames; Polars shares unchanged column buffers between frames, so a
one-column change costs roughly one column. Adequate for spreadsheet-scale data.

### 3.5 `analytics.py`

The frame is registered in an in-memory DuckDB connection as table `dataset`, with
`enable_external_access = false` and `lock_configuration = true` so a query cannot read
files or flip the setting back. Connections are per-call and closed after.

- `run_query(df, sql)` — caller-supplied SQL. Rejected unless: single statement (no inner
  `;`), starts with `SELECT`/`WITH`, contains no mutating keyword
  (`INSERT UPDATE DELETE DROP ALTER CREATE COPY ATTACH INSTALL LOAD PRAGMA CALL EXPORT IMPORT SET …`).
  The registered view is read-only anyway; the keyword list is defence in depth and will
  false-positive on a column literally named `delete` — accepted for Phase 1.
- `aggregate(df, group_by, measure, agg, *, top_n)`, `value_counts(df, column, *, top_n)`,
  `numeric_summary(df, column)`, `timeseries(df, date_column, measure, *, grain, agg)` —
  built SQL. Column names are checked against `df.columns` then double-quoted; values
  travel as bound parameters. No user or model string is ever interpolated.
  `timeseries` accepts ISO-8601 strings (what `standardize_dates` emits) via `TRY_CAST`.

All failures raise `AnalyticsError` (a `ValueError`).

---

## 4. Interface for the frontend team

Import exactly these; nothing else is a stable API.

```python
from src.contracts import DataProfile, ProposedAction, AuditEntry, CLEANING_OPERATIONS, ISSUE_CODES
from src.backend.profiler import load_dataset, profile_dataframe, DatasetLoadError
from src.backend.cleaner import preview, apply, CleaningError
from src.backend.policy import classify, annotate, requires_review, CONFIDENCE_THRESHOLD
from src.backend.audit_logger import AuditLog, RollbackError
from src.backend.analytics import run_query, aggregate, value_counts, numeric_summary, timeseries, AnalyticsError
```

Typical session:

```python
df      = load_dataset(upload.getvalue(), filename=upload.name)   # bytes + name from Streamlit
profile = profile_dataframe(df)                                    # -> the ONLY thing you may send to Claude
log     = AuditLog(df, source_name=upload.name)

for action in planner_output:            # list[ProposedAction] from your Claude call
    action = annotate(action)            # tier re-derived locally; never trust the incoming one
    before = log.current.select(action["target_columns"] or log.current.columns[:4]).head(5)
    after  = preview(log.current, action).select(...).head(5)      # for the review card, local only
    if human_approved(action):
        entry = log.apply(action, human_decision="approved")       # returns AuditEntry, logs before/after

log.rollback(entry["action_id"])          # undo that step and everything after it
log.export("json"); log.export("html")    # change log downloads
cleaned = log.current                     # write_csv() for the download button
```

**What you may forward to an LLM:** `profile_dataframe(df)` — as-is or with keys removed,
never with keys added. **What you must never forward:** the frame, `preview()` output,
`value_counts()` output, anything from `AuditLog.current`. Those are for the user's screen.

---

## 5. Tests & acceptance

`pytest tests/ -v` — **138 tests**. Highlights:

| File | Load-bearing assertions |
|---|---|
| `test_boundaries.py` | No backend file imports a network-capable module, pandas, Streamlit, or `src.frontend`; `contracts.py` imports only `typing` |
| `test_profiler.py` | `json.dumps(profile)` of a frame full of canaries (card number *as an integer column*, email, phone, marker) contains none of them; identifier-like numerics get no aggregates; every detector fires on the fixture and none fires on a clean frame; profile keys are exactly the contract; CSV/bytes/buffer/xlsx ingestion; ragged rows tolerated |
| `test_cleaner.py` | Each operation's result on the fixture; **idempotency for all eight parameterised actions**; unparseable dates kept verbatim; `apply` returns a correct `AuditEntry` and logs the transition; dispatch table equals the contract |
| `test_policy.py` | All four quadrants, both sides of 0.90, malformed inputs raise, `annotate` overrides |
| `test_audit_logger.py` | **Chain → rollback to step 0 → byte-equal to original**; mid-chain rollback; duplicate id rejected; HTML export escapes `<script>` |
| `test_analytics.py` | SELECT/CTE work; 11 non-read-only shapes rejected; `read_csv_auto('…')` refused; odd column names survive quoting; `timeseries` over standardised string dates |

Acceptance for Phase 1 (backend): all of the above green; `messy_sales.csv` goes
load → profile (7 distinct issue codes fire) → standardise/trim/dedupe/impute → audit chain
→ rollback to original → export, entirely offline.

---

## 6. Guidance for the frontend team's LLM layer (informative)

The backend cannot enforce what happens after `profile_dataframe` returns. What it *can*
tell you:

- The profile is safe to transmit as produced. Column names are the one piece of
  user-authored text in it; a header like `Notes_JohnSmith_Overdue` is a residual you
  should disclose to the user before the first call.
- `profiled_at` changes every call. Drop it before sending if you rely on prompt caching.
- Never add `sample_values`, `top_values`, `examples`, or a string column's `min`/`max` to
  what you send "so the model can see the format". Encode the observation as an issue code
  instead; that is what `issues_detected` is for. If you need a code that doesn't exist,
  ask for it here rather than sending a value.
- `confidence` and `business_impact` from the model are inputs to `policy.classify`, not
  decisions. Call `annotate()` on every action before showing a tier to the user.

---

## 7. Implementation record (2026-09-20)

**Built and green:** everything in §1's backend rows — subject to §7.1.

**Deviations from the original brief (`AGENT_1_BACKEND.md`):**
- `standardize_dates` keeps unparseable values instead of nulling them. The brief was
  silent; "never destroy original data" (proposal §5.9) decided it.
- Added `analytics.timeseries` and `numeric_summary` beyond the brief's "query runner" —
  the frontend's charts need them and building them here keeps SQL out of the UI.
- Added `policy.annotate` so re-derivation is one call rather than a pattern each caller
  must remember.
- `xlsxwriter` added to `requirements.txt` — test-only, for the xlsx ingestion round-trip.

**Open items:**
1. **DuckDB ↔ Polars stability on this machine** — §7.1.
2. The mutating-keyword denylist in `run_query` can false-positive on column names. If
   real SME headers hit it, switch to a DuckDB `EXPLAIN`-based statement-type check.
3. `OUTLIER_IQR_MULTIPLIER = 3.0` (extreme outliers) for *detection*; the cleaner's
   `flag_outliers` defaults to `1.5`. Deliberate — detection should be quiet, flagging is
   a human-requested action — but worth revisiting with real data.

### 7.1 DuckDB ↔ Polars access violation — resolved

**Symptom.** `analytics.aggregate` on the fixture frame raised a Windows access violation
(`0xC0000005`) inside `con.execute` — `duckdb 1.5.5`, `polars 1.44.2`, `pyarrow` present,
Python 3.14.7, Windows 11. One earlier run had instead returned a mis-ordered result: memory
corruption, not a logic bug. A synthetic frame with no nulls did not reproduce it.

**Bisect (isolated subprocesses, 3–4 runs each, crash dialogs suppressed via `SetErrorMode`):**

| Registration | Result path | Outcome |
|---|---|---|
| `con.register(name, polars_df)` | `.pl()` / `.fetchall()`, with or without params / settings | **crash 4/4** |
| `con.register(name, polars_df.rechunk())` | `.pl()` | crash |
| `con.register(name, polars_df.to_arrow())` | `.pl()` / `.fetchall()` | **OK 4/4** |
| `con.register(name, polars_df.to_arrow(compat_level=oldest))` | `.pl()` | **OK 4/4** |

The fault is in DuckDB consuming the Polars frame's Arrow C stream directly (likely the
`string_view` path with null bitmaps); going through a materialised pyarrow Table is stable.

**Mitigation.** `analytics._connection` registers
`df.to_arrow(compat_level=pl.CompatLevel.oldest())`. `pyarrow` is now an explicit
requirement. `test_registration_is_stable_across_repeated_queries` guards the regression.

**Not done:** upstream report. Worth filing against DuckDB with the fixture once the
frontend team confirms the same stack; the repro is a 24-row CSV.
