# AGENT 2: Frontend Orchestration & Claude Planning Lead

> **Status:** Built and integrated 2026-09-20. The backend is frozen; this brief is the
> scope boundary. The binding technical contract is [`../LOCAL_INTEGRATION_CONTRACT.md`](../LOCAL_INTEGRATION_CONTRACT.md)
> (schemas, frozen imports, issue register) and the frontend specification is
> [`implementations/PHASE_1_FRONTEND.md`](implementations/PHASE_1_FRONTEND.md).

## Role & Scope

You are **Agent 2**. You own the Streamlit interface, the Anthropic Claude planning layer,
the Human-in-the-Loop review queue, the Strict/Semi autonomy toggle, and the visualisation
engine. You call the backend; you never reimplement it.

**Strict file ownership:**

- `app.py`
- `src/frontend/schemas.py` — Pydantic mirrors of `src/contracts.py` (validation only)
- `src/frontend/prompts.py` — system prompt + the `propose_execution_plan` tool schema
- `src/frontend/mcp_planner.py` — `sanitize_profile`, `generate_execution_plan`, `profile_fingerprint`
- `src/frontend/autonomy.py` — the Strict-mode toggle over `src.backend.policy`
- `src/frontend/charts.py`
- `src/frontend/components/{upload,sidebar,review_card}.py`
- `tests/test_frontend.py`, `tests/test_integration.py`
- `data/samples/messy_sales.csv` — the 50-row demo file for the UI

**Do NOT modify** `src/contracts.py`, anything under `src/backend/`, or the backend tests.
Request changes through §2 of the integration contract.

**Ecosystem rule:** Anthropic Claude only — `anthropic` Python SDK, Messages API,
`claude-opus-5`. No `openai`, no OpenAI-compatible shim, no LangChain adapter.

## The one rule that outranks the others

The LLM never receives a cell value. The backend guarantees `profile_dataframe()` emits
aggregates only; your job is to guarantee nothing is *added* on the way out. That guarantee is
`sanitize_profile()` — allowlist reconstruction, recursive scan for row-naming keys, raised
**before** the client is touched — not prompt text. The exact string handed to the Anthropic
client is asserted canary-free in `tests/test_integration.py::test_no_canary_reaches_the_wire_end_to_end`.
If that test fails, nothing ships.

Nothing from `cleaner.preview()`, `cleaner.run()`, `analytics.value_counts()`, or
`AuditLog.current` may enter a Claude payload. Those exist for the human's screen.

## The backend interface you call (frozen)

```python
from src.contracts import DataProfile, ProposedAction, AuditEntry, CLEANING_OPERATIONS, ISSUE_CODES
from src.backend.profiler import load_dataset, profile_dataframe, DatasetLoadError
from src.backend.cleaner import run, preview, apply, CleaningError
from src.backend.policy import classify, annotate, requires_review, CONFIDENCE_THRESHOLD
from src.backend.audit_logger import AuditLog, RollbackError
from src.backend.analytics import run_query, aggregate, value_counts, numeric_summary, timeseries, AnalyticsError
```

Absolute `src.*` imports only. Never `sys.path.append` — a second import root loads every
backend class twice and `isinstance` checks across the boundary silently fail.

## What Claude may emit, and what it may not

Claude emits `PlannedAction`s: `operation`, `target_columns`, `parameters`, `confidence`,
`business_impact`, `rationale`. It **never** emits `action_id` (minted locally, `uuid4().hex`)
or `autonomy_tier` / `requires_review` (derived by `src.backend.policy.annotate`). The tool
schema is `strict: True` with `additionalProperties: False`, so a model that tries to hand
itself a tier is rejected at the wire. The plan envelope (`plan_id`, `dataset_summary`,
`analysis_questions`) is yours; the backend consumes actions one at a time.

## Phase record

| Phase | Deliverable | State |
|---|---|---|
| 1 — UI shell & contracts | `app.py` four tabs, upload, sidebar, `schemas.py` mirroring `contracts.py` with a drift test | Done |
| 2 — Claude planner & guardrail | `prompts.py`, `mcp_planner.py`, `autonomy.py`; refusal handling, single retry, wholesale rejection | Done |
| 3 — Review queue, charts, audit UI | `review_card.py` with local before/after preview via `cleaner.run`, auto-apply of `automatic` tier, Audit Trail tab with per-step rollback, downloads | Done |
| Integration | `tests/test_integration.py`: load → profile → plan → policy → apply → audit → DuckDB → chart → export → rollback, plus headless `AppTest` boot | Done |
| Live verification | First live Claude call on the demo file; calibration checked — no revenue imputation proposed, dedup blocked as high-impact (contract INT-002 / INT-003, n=1) | Done 2026-09-20 |

## Execution

- `launch.bat` / `./launch.sh` — provision venv, install, run the suite, start the app
- `streamlit run app.py`
- `pytest tests/test_frontend.py tests/test_integration.py -v`
- Branch `feature/ui-orchestration`; never commit to `main`; zero failures before any commit.
