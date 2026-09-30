# CLAUDE.md — AI Data Operations & Analyst Platform for SMEs

## 1. Project Mandate
Build an SME-focused AI Data Operations Platform that ingests, profiles, cleans, validates, and analyzes messy spreadsheets (CSV/Excel) with controlled autonomy (Automatic when safe, HITL when uncertain, Explainable always).

## 2. Environment & Tooling Guidelines
- **Language & Stack:** Python 3.10+, Streamlit, Polars, DuckDB, Plotly Express, Anthropic Claude SDK (`anthropic`), FastMCP, Loguru, pytest.
- **LLM Provider Rule:** Anthropic Claude **exclusively** — the `anthropic` Python SDK against the Messages API, model `claude-opus-5`. `openai` and any OpenAI-compatible shim are prohibited dependencies.
- **Package Manager:** `pip` with `virtualenv` (`venv`).
- **Data Engine Preference:** Use Polars for fast local I/O and DuckDB for dynamic query analytics. Do NOT use Pandas unless strictly necessary for Streamlit edge-case displays.
- **LLM Privacy Rule:** NEVER send raw data rows to the LLM API. Only pass sanitized JSON data profiles (column names, statistical summaries, null percentages, data types).

## 3. Strict Development Rules
1. **File Generation & Structure:**
   - Always create modular files inside designated directories (`src/backend`, `src/frontend`, `tests`).
   - Do NOT create giant single-file monolithic applications.
2. **Terminal & Git Commands:**
   - Always execute terminal commands directly to verify files and run tests.
   - Do NOT commit directly to `main`. Always create feature branches: `feature/backend-engine` or `feature/ui-orchestration`.
   - Before requesting a git commit, run pytest and ensure zero failures.
3. **Reversibility & Logging:**
   - Every data transformation must log a before/after state transition in `audit_logger.py`.
4. **Integration:**
   - `src/contracts.py` is the wire format between backend and frontend. Change it only by agreement, and update `LOCAL_INTEGRATION_CONTRACT.md` §1 in the same commit.
   - One import root: `from src.backend...`, `from src.frontend...`, `from src.contracts import ...`. Never `sys.path.append`.

## 4. Execution & Testing Commands
- Activate Venv: `source venv/bin/activate` (Mac/Linux) or `venv\Scripts\activate` (Windows)
- Run App: `streamlit run app.py` — or `launch.bat` / `./launch.sh`, which provision, test, then launch
- Run Tests: `pytest tests/ -v`
- Run Specific Test File: `pytest tests/test_profiler.py -v`
- Integration only: `pytest tests/test_integration.py -v`
