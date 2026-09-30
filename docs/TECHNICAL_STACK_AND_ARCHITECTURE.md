> **⚠️ PARTIALLY SUPERSEDED.** The LLM-provider rows below (OpenAI / Ollama / LangChain /
> Instructor) are **no longer accurate**. This project is **Anthropic Claude exclusive** —
> the `anthropic` Python SDK against the Messages API. OpenAI dependencies are prohibited.
> Authoritative sources: [`CLAUDE.md`](../CLAUDE.md) §2 and
> [`implementations/PHASE_1_BACKEND.md`](implementations/PHASE_1_BACKEND.md).
> Everything else in this document (Polars/DuckDB engine, Streamlit, Plotly, the pipeline
> flowchart) remains current.

# Technical Stack, Tools & Architecture Pipeline
## Open-Source & Fast-Deploy Ecosystem

---

## 1. Tooling Options & Recommendations

### Option A: Ultra-Fast Python Stack (Recommended for 4-Hour MVP)
- **Frontend / Dashboard UI:** Streamlit or Reflex (Fast Python-only UI rendering).
- **Data Processing Engine:** Polars + DuckDB (Blazing fast in-memory query and manipulation).
- **LLM / Tool Integration:** FastMCP / Instructor / LangChain (Structured JSON schema outputs).
- **Visualization:** Plotly Express (Interactive charts natively supported by Streamlit).
- **State & Logging:** SQLite + Loguru (Zero-config local persistence).

### Option B: Node / React Full-Stack (Alternative)
- **Frontend:** Next.js (Tailwind CSS, Shadcn UI).
- **Backend / MCP Server:** Node.js Express with `@modelcontextprotocol/sdk`.
- **Data Engine:** DuckDB WebAssembly / Node-DuckDB.
- **Charts:** Recharts or Chart.js.

---

## 2. Recommended MVP Tech Stack (Option A)

| Component | Selected Technology | Why Chosen | Cost / Setup Time |
| :--- | :--- | :--- | :--- |
| **Framework** | **Streamlit** | Built-in data tables, file uploaders, state management | Free / 5 mins setup |
| **Data Engine** | **DuckDB & Polars** | Zero external DB overhead; fast on multi-million row files | Free / Instant `pip install` |
| **LLM Provider** | **OpenAI API / Anthropic / Local Ollama** | Structured JSON generation for tool plans | Pennies per query / Free if local |
| **Orchestration** | **Model Context Protocol (MCP)** | Standardized tool calling and separation of concerns | Free open-source standard |
| **Visualization** | **Plotly** | Clean visual outputs rendered directly in UI | Free / Native Python |

---

## 3. Detailed Data Pipeline Flowchart

[ User Uploads .xlsx / .csv ]
|
v
[ Polars Data Profiler ]
|
+---> Generates Data Profile (Null counts, Types, Stats)
|
v
[ MCP / LLM Planning Layer ]
|
+---> Receives JSON profile (No raw PII/Rows sent)
+---> Emits Proposed Cleaning & Analysis Plan
|
v
[ Policy / Safety Matrix ]
/

(Auto-Approved)    (Requires Review)
/

v                    v
[ DuckDB Execution ]   [ Streamlit HITL Queue UI ]
|                    |
|             (User Approves / Rejects)
|                    |
+<-------------------+
|
v
[ Transformed Dataset & Audit History ]
|
+---> Plotly Visualizations & Insights Generation
+---> Export Options (.csv, .xlsx, HTML Audit Log)