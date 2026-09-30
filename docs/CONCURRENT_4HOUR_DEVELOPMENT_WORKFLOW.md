> **⚠️ PARTIALLY SUPERSEDED.** Every reference below to `openai` / Ollama as the LLM layer
> is **obsolete** — this project is **Anthropic Claude exclusive** (`anthropic` Python SDK,
> Messages API). Read `openai` as `anthropic` throughout. Authoritative sources:
> [`CLAUDE.md`](../CLAUDE.md) §2 and
> [`implementations/PHASE_1_BACKEND.md`](implementations/PHASE_1_BACKEND.md).
> The hour-by-hour allocation and the Developer A ↔ B handshake interfaces remain current,
> though the canonical contract now lives in `src/contracts.py`.

# 4-Hour Concurrent Development Sprint Plan
## Concurrent Team Allocation: Developer A & Developer B

---

## 1. Concurrent Task Allocation Matrix
# 4-Hour Concurrent Development Sprint Plan
## Concurrent Team Allocation: Developer A & Developer B

---

## 1. Concurrent Task Allocation Matrix

  HOUR 1                   HOUR 2                   HOUR 3                   HOUR 4
+-----------------------+-----------------------+-----------------------+-----------------------+
| DEV A: Data Pipeline  | DEV A: Execution Engine| DEV A: HITL Logic     | DEV A & DEV B:        |
| Profiler & Cleaning   | & DuckDB Analytics    | & Audit Log Export    | Integration, Testing  |
+-----------------------+-----------------------+-----------------------+ & Final Documentation |
| DEV B: UI Setup &     | DEV B: MCP / LLM      | DEV B: Viz Components |                       |
| Layout Components     | Planner & Prompts     | & Review Cards        |                       |
+-----------------------+-----------------------+-----------------------+-----------------------+


---

## 2. Step-by-Step Hourly Breakdown

### Hour 1: Core Setup & Module Interfaces
* **Developer A (Backend & Data Engine):**
  1. Initialize Python virtual environment and install core packages: `streamlit polars duckdb plotly openai fastmcp`.
  2. Build `profiler.py`: Function that accepts a DataFrame and outputs a JSON schema summarizing column types, missing value percentages, unique counts, and basic numeric distributions.
  3. Build `cleaner.py`: Deterministic functions for exact deduplication, whitespace stripping, date string parsing, and null value handling.

* **Developer B (Frontend & Layout):**
  1. Initialize Streamlit application structure (`app.py`).
  2. Implement file upload widget accepting `.csv` and `.xlsx`.
  3. Create UI layout placeholders: Top navbar, File Summary Sidebar, Dataset Tab, Review Queue Tab, and Insights/Charts Tab.

---

### Hour 2: Orchestration & Execution Logic
* **Developer A (Data Engine & Analytics):**
  1. Build `analytics.py`: DuckDB query runner taking raw SQL or execution plans and producing summarized tables for visualization.
  2. Create reversible state tracker (`audit_logger.py`) to record every applied transformation and support step rollbacks.

* **Developer B (LLM Planner & Prompts):**
  1. Build `mcp_planner.py`: Function using structured JSON output from OpenAI/Anthropic/Ollama.
  2. Feed data summary JSON into the prompt to generate a structured cleaning and analysis plan without sending full raw rows.
  3. Map LLM outputs into executable function calls.

---

### Hour 3: Human-in-the-Loop Queue & Visualizations
* **Developer A (HITL Queue & Export System):**
  1. Wire approval/rejection state in Streamlit (`st.session_state`).
  2. Implement side-by-side diff preview for flagged records in the review queue.
  3. Add file export handlers (Download Cleaned CSV, Download Change Log JSON/HTML).

* **Developer B (Dynamic Visualizations & UI Integration):**
  1. Build `charts.py`: Plotly visualization engine that generates bar, line, and scatter plots based on analytics output.
  2. Render quality scores, confidence badges, and plain-language summary cards in the UI.

---

### Hour 4: Integration, End-to-End Testing & Handshake
* **Developer A & Developer B (Joint Execution):**
  1. **Minute 0–20:** Connect `mcp_planner.py` with `profiler.py` and Streamlit UI.
  2. **Minute 20–40:** Test end-to-end flow using sample messy datasets (e.g., messy sales CSVs with mixed dates, duplicates, missing IDs).
  3. **Minute 40–60:** Polish UI feedback, handle edge-case errors gracefully, and document local startup instructions in `README.md`.

---

## 3. Developer Handshake Interfaces (Contract Definition)

To work concurrently without code collisions, both developers must adhere to these standard data structures:

### Interface 1: Data Profile Object (`Developer A` -> `Developer B`)
```json
{
  "total_rows": 1250,
  "total_columns": 5,
  "columns": [
    {
      "name": "transaction_date",
      "type": "String",
      "null_count": 12,
      "unique_values": 450,
      "issues_detected": ["mixed_date_formats"]
    }
  ]
}