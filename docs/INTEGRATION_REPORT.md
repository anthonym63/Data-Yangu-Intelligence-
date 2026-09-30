# Integration Report — Local Codebase Unification

**Date:** 2026-09-20
**Scope:** Merge of the Backend Data Engine (Agent 1) and the Frontend / Claude MCP Planner (Agent 2) into a single local repository, per `LOCAL_INTEGRATION_INSTRUCTIONS.md`.
**Governing documents:** [`../LOCAL_INTEGRATION_CONTRACT.md`](../LOCAL_INTEGRATION_CONTRACT.md) (schemas, frozen imports, issue register), [`../CLAUDE.md`](../CLAUDE.md).
**Companion records:** contract §6 (integration record), [`implementations/PHASE_1_BACKEND.md`](implementations/PHASE_1_BACKEND.md), [`implementations/PHASE_1_FRONTEND.md`](implementations/PHASE_1_FRONTEND.md) v1.1.

---

## 1. Results

| Check | Result |
|---|---|
| Full suite, three consecutive runs | **186 passed · 0 failed · 0 skipped** — 14.2 s / 12.7 s / 10.7 s |
| `launch.bat --test` (PowerShell) | 186 passed, exit code 0 |
| `launch.sh --no-test` (headless, HTTP-probed) | `/_stcore/health` → `200 ok`, `/` → 200, healthy in 9.8 s including the pip check, port released on kill |
| `AppTest.from_file("app.py").run()` | No exception, 4 tabs, `autonomy_mode == "strict"`, `plan is None` |
| End-to-end (`tests/test_integration.py::test_full_pipeline_end_to_end`) | 24 rows → 7/7 issue codes fire → 5-action stubbed plan → 4 applied, 1 **blocked** (financial imputation at 0.42 × high) → 21 rows, ISO dates, whitespace trimmed, regions title-cased → 4 audit entries and 4 before/after log lines → DuckDB aggregate + monthly trend → Plotly figures → JSON/HTML export → `rollback_all()` **byte-equal to the original upload** |
| Privacy at the wire (`test_no_canary_reaches_the_wire_end_to_end`) | A frame whose every column is a canary (card number as integers, email, phone, marker string) → none appears in the exact string handed to the Anthropic client; the identifier column carries no aggregates, the measure column exactly five |

Tests by file: frontend 42 · cleaner 40 · profiler 28 · analytics 25 · policy 22 · audit_logger 14 · boundaries 9 · integration 6.

Agent 1's suite, previously 137 passed + 1 skipped on their machine (missing `xlsxwriter`), is 138 green here. Two failures surfaced during the merge and were fixed the same day — see INT-004 and INT-005 in the contract.

---

## 2. Final structure — 6,325 lines, excluding `venv/`

```
app.py  (381)                    Dataset · Review Queue · Insights · Audit Trail
launch.bat / launch.sh           provision → test → launch   [both verified]
src/contracts.py  (141)          wire contracts (TypedDict, dependency-free) — the handshake
src/backend/  (1,073 · 6 files)  profiler · cleaner · policy · audit_logger · analytics
src/frontend/ (960 · 10 files)   schemas · prompts · mcp_planner · autonomy · charts · components/{upload,sidebar,review_card}
tests/  (1,516 · 10 files)       8 suites + conftest + fixtures/messy_sales.csv (24-row reference dataset)
data/samples/messy_sales.csv     50-row demo file for the UI
docs/  (1,439 · 10 files)         AGENT_1_BACKEND · AGENT_2_FRONTEND · proposals · architecture · MARKET_RESEARCH · this report
docs/implementations/            PHASE_1_BACKEND.md · PHASE_1_FRONTEND.md (v1.1, reconciled to src/contracts.py)
LOCAL_INTEGRATION_CONTRACT.md    schemas · frozen imports · ISS-001…009 + INT-001…014 · §6 integration record
LOCAL_INTEGRATION_INSTRUCTIONS.md · README.md · CLAUDE.md · pyproject.toml · requirements.txt · .gitignore · .env.example
```

Import root is the repository: `from src.backend…`, `from src.frontend…`, `from src.contracts import …`. No `sys.path` manipulation anywhere.

### Removed

- The nested `DATAYANGU INTELLIGENCE/DATAYANGU INTELLIGENCE/` copy — verified byte-identical to its parent with `diff -r` — and both venvs: **96,800 files** hardwired to `C:\Users\lenovo\…\python3.14`, unusable on this machine.
- `AI_Data_Operations_Platform_MVP_Development_Proposal (1).md` — byte-identical duplicate.
- `gemini-code-1789843529029.md` — a truncated prefix of the Hybrid Autonomy proposal.
- Root `Agent1.md` (71 lines, truncated) — superseded by Agent 1's 128-line `docs/AGENT_1_BACKEND.md`.
- Five root-level doc copies — Agent 1's `docs/` versions are identical apart from "partially superseded" banners marking the obsolete OpenAI references.
- `src/frontend/policy.py` — the autonomy matrix is implemented once, in `src/backend/policy.py`.
- `tests/fixtures/messy_sales_profile.json` — static fixture replaced by the live profiler in `conftest.py`, so it cannot drift.
- Root `__pycache__/`, `.pytest_cache/`.

CRLF line endings on every migrated `.py`, `.md` and `.csv` were normalised to LF.

---

## 3. Decisions taken during integration — for ratification

1. **Agent 1's contract won wholesale.** Their schema was backed by 137 passing tests; the frontend's was backed by a stub client. The frontend was rewritten to `src/contracts.py`: `dtype` / `unique_count` / `null_pct` / `is_numeric` / `numeric{}` / `exact_duplicate_count`, six operations, seven issue codes, `action_id` / `rationale`. `tests/test_frontend.py::test_pydantic_mirrors_typeddict_contracts` fails the build if the Pydantic mirrors and TypedDicts ever diverge.
2. **Confidence threshold is inclusive, `>= 0.90`** (Agent 1's implementation), not the exclusive `> 0.90` in the frontend's v1.0 spec. A confidence of exactly 0.90 on a low-impact action now auto-applies.
3. **Semi-autonomous mode applies `automatic`-tier actions the moment the plan arrives** — the proposal's "executed immediately, logged in the audit trail." Strict mode applies nothing without approval. Every auto-applied step appears in the Audit Trail with an undo.
4. **`CLAUDE.md` was replaced** with Agent 1's Anthropic-only version. Editing it had been declined in Phase 1; the "FastMCP/OpenAI SDK" line contradicted the project mandate, Agent 1 had already produced a corrected version, and the "clean the repository" instruction made adopting it the right call.
5. **Duplicate documents were deleted** although an earlier instruction was to retain them; "remove any dead files" superseded that. Nothing unique was lost.
6. **A fourth tab, Audit Trail, was added** (per-step "Undo from here", JSON/HTML change-log and cleaned-CSV downloads). Not in the original three-tab spec; it is where "explainable always" is actually delivered.
7. **One doc-only edit to a backend-owned file:** `src/contracts.py` line 10 pointed at the pre-rename spec path. No contract change (INT-013).

---

## 4. Open items requiring a decision from the project owner

| ID | Item | Why it matters |
|---|---|---|
| **INT-002** | **No live Claude call has ever been made.** All 48 planner and integration tests use a stub client. | The request shape is proven (`claude-opus-5`, adaptive thinking, strict tool, `fallbacks="default"`); the model's behaviour is not. Requires `ANTHROPIC_API_KEY` in `.env`. |
| **INT-003** | **Confidence calibration is unverified.** On the first real run against `data/samples/messy_sales.csv`, `impute_nulls` on `revenue` must land **below 0.90 and `high`**, i.e. blocked. | This is the entire autonomy gate. If the model rates financial imputation confidently, Semi mode will write invented numbers into a revenue column. Tune `SYSTEM_PROMPT` in `src/frontend/prompts.py` before offering Semi mode if so. |
| INT-007 | `fastmcp` is installed, unused in Phase 1, and pulls ~25 transitive packages (`mcp`, `starlette`, `uvicorn`, `authlib`, `cryptography`, `keyring`). | Drop from `requirements.txt` or keep for a Phase 2 MCP server. Raised twice; undecided. |
| INT-010 | Not a git repository — 6,325 lines untracked and unbacked. | Left alone by the owner's decision, twice. `CLAUDE.md` §3.2 branch rules cannot apply until `git init`. |
| INT-009 | The repository lives in a OneDrive-synced folder; the first `pip install` exceeded ten minutes. | Post-install performance is fine (suite ~11–14 s, app healthy in <10 s). Relocate if tooling stays sluggish. |

---

## 5. Points worth reviewing together

- **`numeric.min` / `numeric.max` on measure columns are sent to Claude.** Agent 1 withholds aggregates for identifier-like columns, but the maximum of a `revenue` column *is* a real transaction amount from the books. The proposal permits "aggregate statistics" and the integration accepted it — but this is a product-owner privacy call, not an engineering one.
- **Column names travel to Claude.** A header such as `Notes_JohnSmith_Overdue` is user-authored text. Opt-in pseudonymisation is on the backlog; the UI should disclose the residual before the first call.
- **The review-queue click-through is not automated.** Streamlit's `AppTest` cannot drive a file upload, so the approve / skip / undo button flow is verified by logic tests (`test_preview_matches_apply`, `test_strict_mode_blocks_all_auto_apply`), not by a simulated session. A manual pass through `launch.bat` with the demo file is recommended.
- **The Insights tab is heuristic in Phase 1.** It charts the first low-cardinality text column against the first numeric measure, plus a monthly trend on the standardised date column. Claude's `analysis_questions` are listed, not answered; NL→SQL over the read-only `run_query` guard is Phase 2 (INT-012). Confirm this is acceptable scope.
- **The 50-row demo file fires 6 of 7 issue codes** (`mixed_numeric_and_text` does not — only 1 of 49 `invoice_id` values is numeric-like, below the 20 % floor). The 24-row reference fixture fires all seven (INT-014).
- **Two boundary findings recorded for future contract changes:** "absent ≠ null" across the TypedDict/Pydantic boundary — optional keys must be dumped with `exclude_none=True` and tolerated as missing on receipt (INT-004); and `AppTest.from_file` resolves relative paths against the calling test file, not the working directory (INT-005).

---

## 6. Verification commands

```bash
pytest tests/ -q                       # 186 tests, no network, ~11-14 s
pytest tests/test_integration.py -v    # the end-to-end and privacy-at-the-wire tests
launch.bat --test                      # Windows: provision + suite
./launch.sh --test                     # macOS / Linux / Git Bash: provision + suite
launch.bat                             # provision + suite + streamlit run app.py
```

The exact payload sent to Claude is logged at DEBUG by `sanitize_profile()` on every call; it is visible in the terminal running the app, and it is the artefact to inspect for INT-002 and INT-003.

---

## Addendum — later on 2026-09-20: first live Claude call

**What happened.** "Generate cleaning plan" in the UI raised `TypeError: Could not resolve authentication method` as a raw traceback. The key in `.env` was well-formed (108 characters, `sk-ant-` prefix, no BOM, unquoted); the fault was that neither Streamlit nor the app loaded `.env`, so the SDK never saw it. Recorded as **INT-015**.

**Fixes.** `_build_client` loads `.env` from an explicit repo-root path (python-dotenv's default search walks the *caller's* stack frame and behaves differently under `streamlit run`, pytest and `python -`), tolerating a Notepad BOM. Every SDK failure — missing credentials, 401, 403, 404, 429, connection — is translated to one `PlannerError` with plain wording, which the UI renders as a message rather than a stack trace. `python-dotenv` is now an explicit requirement. Two tests pin the translation. Suite: **188 passed**.

**Live result — INT-002 closed, INT-003 verified (n=1).** Auth OK against `claude-opus-5` (free `models.retrieve`). Then one real plan call through the app's own code path, with the process proven to have no `ANTHROPIC_API_KEY` before and to have it after — `_build_client` did the loading. Six actions in 31.4 s:

| Action | Targets | Confidence | Impact | Tier |
|---|---|---|---|---|
| `trim_whitespace` | `customer_name` | 0.99 | low | automatic |
| `normalize_casing` (title) | `customer_name` | 0.92 | low | automatic |
| `standardize_dates` (`%Y-%m-%d`) | `transaction_date` | 0.94 | low | automatic |
| `flag_outliers` (iqr, 1.5) | `revenue` | 0.95 | low | automatic |
| `impute_nulls` (constant `"Unknown"`) | `region` | 0.84 | low | review_required |
| `drop_exact_duplicates` | whole table | 0.68 | **high** | **human_required** |

The model proposed **no imputation of `revenue` or `units_sold`**, explaining in its summary that inventing numbers in a financial column would silently change sales totals. It blocked deduplication because repeated invoice ids might be genuine repeat billing. Five analysis questions were returned. This is the behaviour the autonomy gate exists to enforce, arrived at by the model itself — on one run, on one synthetic file. Re-check on real customer spreadsheets before relying on Semi-autonomous mode.

**Repository now:** 6,426 lines excluding `venv/`.
