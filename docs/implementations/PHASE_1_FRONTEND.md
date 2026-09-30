# PHASE 1 IMPLEMENTATION SPECIFICATION — FRONTEND & CLAUDE PLANNER
## AI Data Operations & Analyst Platform for SMEs

**Version:** 1.1 — reconciled to `src/contracts.py` at integration (2026-09-20). Supersedes 1.0, which specified schemas that the built backend did not adopt; §8 records every change.
**Status:** Implemented and integrated. Open items in §9.
**Owning team:** Frontend / Agent 2
**Counterpart:** [`PHASE_1_BACKEND.md`](PHASE_1_BACKEND.md) — the backend engine this layer calls

---

## 0. Purpose & invariants

This document is the frontend half of the contract. Schemas and backend signatures live in
[`../../LOCAL_INTEGRATION_CONTRACT.md`](../../LOCAL_INTEGRATION_CONTRACT.md) §1 and are not
repeated here; this document covers what only the frontend decides: how Claude is called, what
it may see, how its output is validated, how the autonomy toggle behaves, and how the UI holds
state.

Three invariants:

> **1. Keys removed, never keys added.** `profile_dataframe()` output is the only thing that
> may reach Claude. `sanitize_profile()` rebuilds it from an allowlist and raises before any
> network call if a row-naming key is present at any depth. Enforced by the canary test at the
> wire in `tests/test_integration.py`.

> **2. The model proposes; it never grants itself a permission.** Claude emits confidence and
> impact. `action_id` is minted locally; `autonomy_tier` is derived by `src.backend.policy`.
> The tool schema is strict, so a model-supplied tier is rejected as malformed.

> **3. A plan is accepted whole or not at all.** One invalid action discards the plan. A
> partially-valid plan means the model misread the schema — exactly when its other actions
> deserve the least trust.

---

## 1. Ownership

| Path | Purpose |
|---|---|
| `app.py` | Four tabs (Dataset · Review Queue · Insights · Audit Trail), session state, auto-apply |
| `src/frontend/schemas.py` | Pydantic mirrors of the TypedDicts in `src/contracts.py`; drift-tested |
| `src/frontend/prompts.py` | `SYSTEM_PROMPT`, `PROPOSE_EXECUTION_PLAN_TOOL` |
| `src/frontend/mcp_planner.py` | `sanitize_profile`, `profile_fingerprint`, `generate_execution_plan` |
| `src/frontend/autonomy.py` | `apply_strict_mode`, `effective_tier` — the UI toggle over the backend matrix |
| `src/frontend/charts.py` | `bar_chart`, `line_chart`, `scatter_chart` over `list[dict]` rows |
| `src/frontend/components/` | `upload`, `sidebar`, `review_card` |
| `tests/test_frontend.py`, `tests/test_integration.py` | |

Off-limits: `src/contracts.py`, `src/backend/**`, backend tests.

---

## 2. Schemas — `src/frontend/schemas.py`

`src/contracts.py` is TypedDict and dependency-free by design. The frontend needs one thing
TypedDicts cannot do — *validate* what Claude sends back — so it carries Pydantic mirrors with
`extra="forbid"`:

| Pydantic | Mirrors | Role |
|---|---|---|
| `NumericAggregates`, `ColumnProfile`, `DataProfile` | `contracts.*` key-for-key | Validate Interface 1 on receipt (first privacy interlock) |
| `PlannedAction` | — | What Claude may emit: `operation`, `target_columns`, `parameters`, `confidence`, `business_impact`, `rationale` |
| `ProposedAction(PlannedAction)` | `contracts.ProposedAction` | Adds `action_id`, `autonomy_tier`, `requires_review` — filled locally |
| `PlannerOutput` | tool input | `dataset_summary`, `actions: list[PlannedAction]`, `analysis_questions ≤ 5` |
| `ExecutionPlan` | — | Frontend envelope: `plan_id`, `dataset_summary`, `actions: list[ProposedAction]`, `analysis_questions`. `actions_as_contract()` yields plain dicts for the backend |

Vocabularies are imported from `src.contracts` (`CLEANING_OPERATIONS`, `ISSUE_CODES`), never
retyped. `tests/test_frontend.py::test_pydantic_mirrors_typeddict_contracts` fails the build if
any mirror's field set diverges from its TypedDict.

`target_columns` may be empty only for `drop_exact_duplicates` (contract §1B).

---

## 3. Claude API contract — `mcp_planner.generate_execution_plan`

Model **`claude-opus-5`**, official `anthropic` SDK, imported lazily so tests and cold start
never pay for it.

```python
client.beta.messages.create(
    model="claude-opus-5", max_tokens=16000, system=SYSTEM_PROMPT,
    thinking={"type": "adaptive"},                       # no budget_tokens - 400 on Opus 5
    tools=[PROPOSE_EXECUTION_PLAN_TOOL],                 # strict: True, additionalProperties: False
    tool_choice={"type": "auto"},                        # + explicit instruction in the prompt
    betas=["server-side-fallback-2026-07-01"], fallbacks="default",
    messages=[{"role": "user", "content": json.dumps(sanitized_profile, sort_keys=True)}],
)
```

Flow: `sanitize_profile` (may raise) → call → if `stop_reason == "refusal"` raise
`PlannerError` **before** reading `content` → no `tool_use` block: retry once, then
`PlannerError` → `PlannerOutput.model_validate` (wholesale rejection) → per action: mint
`action_id = uuid4().hex`, `annotate()` via backend policy → `ExecutionPlan`.

Tool enum is `sorted(CLEANING_OPERATIONS)` so the cached prompt prefix is byte-stable.

Errors to catch upstream: `PrivacyViolationError`, `PlannerError`, and the SDK chain
`NotFoundError → RateLimitError → APIStatusError → APIConnectionError`.

---

## 4. The guardrail — `sanitize_profile`

Allowlists, rebuilt field by field:

- profile: `total_rows`, `total_columns`, `exact_duplicate_count`, `columns`
- column: `name`, `dtype`, `null_count`, `null_pct`, `unique_count`, `is_numeric`, `issues_detected`, `numeric`
- numeric: `min`, `max`, `mean`, `median`, `std` (floats only)

`profiled_at` is deliberately dropped: it is the one non-deterministic field and would defeat
prompt caching (contract §1A). `profile_fingerprint()` hashes the sanitized payload and is the
rerun guard for ISS-007.

Two severities, deliberately different: an unrecognised benign key (`profiling_duration_ms`)
is dropped in silence; a key whose *name* denotes row data (`sample_values`, `sample_rows`,
`top_values`, `mode`, `examples`, `head`, `records`, `preview`, …) raises
`PrivacyViolationError` at any depth. A permitted key with a non-scalar value also raises.
The serialised payload is logged at DEBUG so the claim is auditable.

Residual the guardrail cannot close: column **names** are user-authored text. A header like
`Notes_JohnSmith_Overdue` goes to Claude. Disclose in the UI; pseudonymisation is backlog.

---

## 5. Prompt — `prompts.SYSTEM_PROMPT`

Establishes in order: role and local-execution framing; what the profile contains and the
explicit "you will never be shown raw records; do not ask for them"; the seven issue codes
with one-line meanings; the six operations with parameters and when each applies; calibrated
confidence with worked contrasts (whitespace trim ≈ 0.99; financial imputation well below 0.90;
dedup under `possible_duplicate_key` deserves caution — repeated invoice numbers may be real);
business impact rules; plain-language `rationale` citing the statistic; up to five analysis
questions; and "empty `actions` for clean data — inventing work is a failure."

---

## 6. Autonomy — `src/frontend/autonomy.py`

The matrix is `src.backend.policy.classify` (threshold `>= 0.90`, inclusive) and is **not**
reimplemented. `effective_tier(action, strict)` re-derives the tier from the action's own
confidence and impact via `annotate()` — never trusting what the action carries — then applies
the Strict toggle: `automatic → review_required`, everything else unchanged. Strict mode can
only add friction.

Auto-apply: on plan generation in Semi mode, every `automatic` action is applied through
`AuditLog.apply` immediately and logged — the proposal's "executed immediately, logged in the
audit trail". In Strict mode nothing qualifies.

---

## 7. `app.py` — flow and state

Session keys, declared once in `_init_state()`: `log` (`AuditLog`, owns original + current
frame), `profile`, `plan`, `plan_fingerprint`, `decisions` (`action_id → pending|approved|
rejected`), `applied` (set of ids in the log), `autonomy_mode`, `uploaded_name`,
`upload_digest`.

| Tab | Flow |
|---|---|
| Dataset | `load_dataset(bytes, filename)` → `profile_dataframe` → new `AuditLog`; same-bytes reupload is a no-op (`upload_digest`). Column table. "Generate cleaning plan" → fingerprint guard → `generate_execution_plan` → `_auto_apply` |
| Review Queue | Per action: `effective_tier` → `cleaner.run(log.current, action)` for a **local** before/after slice + `Delta` → card → on approve `log.apply`. `CleaningError` (e.g. column gone after an earlier step) renders on the card, never crashes the run. `Delta.notes` surfaces unparsed dates (ISS-006) |
| Insights | Claude's questions listed; charts are heuristic defaults over `log.current` — `numeric_summary` tiles, `aggregate` by the first low-cardinality text column, `timeseries` on the standardised date column. NL→SQL is Phase 2 |
| Audit Trail | `log.entries` in order with per-step "Undo from here" → `log.rollback`, which also resets `applied`/`decisions` for the undone ids. JSON/HTML change-log and cleaned-CSV downloads |

No `sys.path` manipulation; absolute `src.*` imports; `streamlit run app.py` from the root.

---

## 8. Reconciliation record — 1.0 → 1.1

| 1.0 (this spec's draft) | 1.1 (adopted from `src/contracts.py`) | Why |
|---|---|---|
| `type`, `unique_values` | `dtype`, `unique_count` | Backend built and tested first |
| — | `null_pct`, `is_numeric`, `numeric{min,max,mean,median,std}`, `exact_duplicate_count`, `profiled_at` | Richer aggregates; `numeric` only on measure columns, never identifiers |
| Issue codes `exact_duplicate_rows`, `mixed_types`, `near_duplicate_values`, `outlier_values` | `inconsistent_casing`, `mixed_numeric_and_text`, `possible_duplicate_key`, `numeric_outliers` (+ table-level `exact_duplicate_count`) | Backend detector set |
| 4 operations, `remove_exact_duplicates` | 6 operations, `drop_exact_duplicates`, `+normalize_casing`, `+flag_outliers` | Backend cleaner set |
| `impute_nulls` strategies `zero`, `forward_fill` | `constant` (+`fill_value`), `drop_rows` | What the cleaner implements |
| `steps[]`, `step_id: int`, `reason` | `actions[]`, `action_id: str (uuid4 hex)`, `rationale` | Contract §1B |
| `expected_rows_affected` from the model | `Delta` from `cleaner.run` on the card | Actual computed effect beats a model estimate |
| `src/frontend/policy.py`, threshold `> 0.90` | `src.backend.policy`, threshold `>= 0.90` | One matrix, tested in one place |
| `ExecutionPlan` consumed by backend | Envelope stays frontend-side; backend consumes `ProposedAction` one at a time | Contract §1B |
| Static `messy_sales_profile.json` fixture | Live `profile_dataframe(messy_df)` fixture | Zero drift by construction |

---

## 9. Tests & open items

`tests/test_frontend.py` — guardrail (strip / drop `profiled_at` / raise on raw keys at any
depth / numeric allowlist / canary at the wire / Pydantic forbids extras / fingerprint stable),
mirror drift, tool-enum = contract, schema rejections, planner behaviour (validated plan, local
ids, backend-derived tiers, request shape, refusal, retry, exhaustion, wholesale rejection,
model-supplied tier rejected, empty plan), Strict toggle, charts.

`tests/test_integration.py` — full pipeline on the 24-row fixture (all seven codes fire →
stubbed plan → tiers → apply through `AuditLog` → 4 audit lines → DuckDB aggregate + timeseries
→ Plotly figures → exports → rollback byte-equal to original); canary-free wire on a frame full
of secrets; preview equals apply; Strict blocks all auto-apply; the 50-row demo file flows
through; headless `AppTest` boot with four tabs and clean session state.

**Was open until 2026-09-20 — see §10:** no live Claude call had been made — every planner test uses a stub. The first real
run against `data/samples/messy_sales.csv` must be inspected for calibration: `impute_nulls` on
`revenue` should land below 0.90 and `high`. If it does not, tune §5 before offering Semi mode.

---

## 10. Live verification — 2026-09-20

First real call via `generate_execution_plan(profile)` on `data/samples/messy_sales.csv`, with
`.env` loaded by `_build_client` (INT-015, fixed the same day: Streamlit does not read `.env`, and
python-dotenv's default search walks the caller's stack frame, so the path is now explicit). Six
actions in 31.4 s: four format-only actions `automatic` (0.92–0.99); `impute_nulls(region,
constant "Unknown")` 0.84 → `review_required`; `drop_exact_duplicates` 0.68 × high →
`human_required`. **No imputation of `revenue` was proposed**; the model's summary gave the very
reason §5 asks it to weigh. Calibration matches intent on this sample (n=1) — re-check on real
customer files before offering Semi mode.
