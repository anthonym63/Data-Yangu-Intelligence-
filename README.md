# AI Data Operations & Analyst Platform for SMEs

Upload a messy spreadsheet. Get it profiled, cleaned, analysed and explained — with a human in the loop wherever it matters, and a full undo trail.

**Autonomous when safe. Human when necessary. Explainable always.**

---

## The privacy guarantee

Your data never leaves your machine. Profiling, cleaning and analysis run locally in Polars and DuckDB.

Claude is a *planner*, not a processor. It receives only aggregate statistics — column names, types, null counts, unique counts, detected issue codes, and min/max/mean/median/std for numeric measure columns. Never a record, never a sample, never a min/max of an identifier or a text column.

This is enforced in code at both ends, not by prompt text:

- **Backend** — `profile_dataframe()` in [src/backend/profiler.py](src/backend/profiler.py) cannot emit a cell value; identifier-like columns get no aggregates at all. No module under `src/backend/` may import anything that can open a socket ([tests/test_boundaries.py](tests/test_boundaries.py)).
- **Frontend** — `sanitize_profile()` in [src/frontend/mcp_planner.py](src/frontend/mcp_planner.py) rebuilds the outbound payload from an allowlist and raises *before* any network call if a row-naming key appears at any depth.
- **Proof** — [tests/test_integration.py](tests/test_integration.py) seeds a frame with a card number, an email, a phone number and a marker string, runs the whole pipeline, and asserts none of them appear in the exact string handed to the Anthropic client.

The serialised payload is logged at DEBUG on every call, so the claim is auditable in the terminal running the app.

---

## Quick start

```bat
launch.bat            :: Windows  — create venv if missing, install, run the suite, start the app
./launch.sh           #  macOS/Linux/Git Bash — same
launch.bat --test     :: provision + tests only
launch.bat --no-test  :: provision + start without testing
```

Requires Python 3.10+. For plan generation, copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY` (get one at [console.anthropic.com](https://console.anthropic.com/)). **Everything except "Generate cleaning plan" works without a key** — upload, profiling, cleaning, analytics, audit trail and downloads are entirely local. The app reads `.env` itself when you click Generate (a variable exported in your shell also works), and a plan takes about 30 seconds — Opus 5 thinks before it answers.

Manual equivalent:

```bash
python -m venv venv && venv\Scripts\activate      # source venv/bin/activate on macOS/Linux
pip install -r requirements.txt
pytest tests/ -q                                  # 189 tests, ~17 s, no network
streamlit run app.py
```

A deliberately messy 50-row demo file ships at [data/samples/messy_sales.csv](data/samples/messy_sales.csv): three date formats, three duplicate rows, padded and inconsistently cased names, 30 % missing regions, a malformed invoice ID, and two revenue outliers.

---

## How it works

```
Upload → Profile (Polars, local) → Plan (Claude, aggregates only) → Policy gate → Apply (local) → Audit → Analyse (DuckDB) → Chart
```

1. **Profile.** Seven detectors run in one lazy Polars pass: `mixed_date_formats`, `inconsistent_casing`, `leading_trailing_whitespace`, `high_null_rate`, `mixed_numeric_and_text`, `possible_duplicate_key`, `numeric_outliers`.
2. **Plan.** Claude (`claude-opus-5`, strict tool schema) proposes actions from six deterministic operations — `drop_exact_duplicates`, `trim_whitespace`, `normalize_casing`, `standardize_dates`, `impute_nulls`, `flag_outliers` — each with a confidence, a business impact and a plain-language rationale. It cannot name an operation outside that set, and it cannot grant itself a permission: ids are minted locally and tiers are derived by the backend.
3. **Gate.** Every action is routed by the autonomy matrix:

   | | Low business impact | High business impact |
   |---|---|---|
   | **Confidence ≥ 90 %** | Applied automatically, logged | Sent for your approval |
   | **Confidence < 90 %** | Sent for your approval | **Blocked** — needs a person |

   **Strict** mode (sidebar) sends everything for approval. Each review card shows a local before/after preview computed by the same code that will apply the change.
4. **Audit.** Every applied step records rows before/after, cells changed and the human decision. Any step can be undone from the Audit Trail tab; a full rollback is byte-equal to the original upload. Change logs export as JSON or HTML; the cleaned data as CSV.
5. **Analyse.** DuckDB runs read-only over the cleaned frame (external access disabled, mutating SQL rejected). Charts are Plotly.

---

## Project layout

```
app.py                          Streamlit entrypoint — Dataset · Review Queue · Insights · Audit Trail
launch.bat / launch.sh          Provision, test, launch
src/contracts.py                Wire contracts (TypedDict, dependency-free) — the handshake between the two halves
src/backend/                    Agent 1 — all local, no network
  profiler.py                     load_dataset, profile_dataframe, seven detectors, identifier rule
  cleaner.py                      six pure, idempotent operations; run / preview / apply
  policy.py                       the autonomy matrix (classify, annotate)
  audit_logger.py                 AuditLog: snapshots, rollback, JSON/HTML export
  analytics.py                    read-only DuckDB: run_query, aggregate, value_counts, numeric_summary, timeseries
src/frontend/                   Agent 2 — UI and the Claude planner
  schemas.py                      Pydantic mirrors of contracts.py, drift-tested
  prompts.py                      system prompt + propose_execution_plan tool schema
  mcp_planner.py                  sanitize_profile, generate_execution_plan, profile_fingerprint
  autonomy.py                     Strict-mode toggle over src.backend.policy
  charts.py                       bar / line / scatter builders
  components/                     upload, sidebar, review_card
tests/                          189 tests across 8 files; fixtures/messy_sales.csv is the 24-row reference dataset
data/samples/                   50-row demo file for the UI
docs/                           Proposals, architecture, market research, per-agent charters
docs/implementations/           PHASE_1_BACKEND.md · PHASE_1_FRONTEND.md
LOCAL_INTEGRATION_CONTRACT.md   Schemas, frozen imports, issue register — authoritative
LOCAL_INTEGRATION_INSTRUCTIONS.md  The integration protocol both agents follow
```

Import root is the repo: `from src.backend...`, `from src.frontend...`, `from src.contracts import ...`. No `sys.path` manipulation anywhere — a second import root loads every class twice and `isinstance` checks silently fail across the boundary.

---

## Status

**Verified by running:** the full suite (189 tests: profiler 28 · cleaner 40 · policy 22 · audit 14 · analytics 25 · boundaries 9 · frontend 44 · integration 7), headless `AppTest` runs of `app.py` both empty and with a profile, plan and audit log loaded, a real `streamlit run` through `launch.sh` answering `200 ok` on its health endpoint, and **one live Claude call** against the demo file (contract §6).

**Worth knowing before you trust Semi-autonomous mode:**

- **Calibration has been checked once, on the synthetic demo file.** Claude declined to impute the missing `revenue` values at all — "inventing numbers in a financial column would silently change your sales totals" — and marked whole-table deduplication high-impact at 0.68 confidence, so it was blocked pending a human. That is the intended behaviour, but it is one run on one file. Re-check on two or three real spreadsheets before relying on Semi mode.
- The Insights tab charts heuristic defaults; wiring Claude's analysis questions to SQL is Phase 2.

The full register is §2 of [LOCAL_INTEGRATION_CONTRACT.md](LOCAL_INTEGRATION_CONTRACT.md).

---

## Contributing

- `src/contracts.py` is the handshake. Change it only by agreement, and update the contract's §1 in the same commit.
- `pytest tests/ -q` must pass with zero failures before any commit. Never commit to `main` — branch `feature/backend-engine` or `feature/ui-orchestration`.
- Anthropic Claude only. No `openai` package, no OpenAI-compatible shim.
- Every transformation logs a before/after state transition through the audit logger. Nothing from a preview, a value count or the current frame may ever enter a Claude payload.
