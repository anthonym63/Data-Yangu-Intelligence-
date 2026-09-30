> **Read this first.** The body below is the integration protocol exactly as issued. Two of its
> illustrative snippets predate the built backend and must not be copied literally:
>
> 1. The **Profiler Output Schema** example includes `sample_values`. The platform's privacy rule
>    (`CLAUDE.md` §2, spec §0) forbids any cell value — anonymised or not — in a profile, and the
>    test suite (`tests/test_profiler.py`) fails if one appears. The real schema is in
>    `LOCAL_INTEGRATION_CONTRACT.md` §1A and has no such field.
> 2. The **Python Path Setup** snippet names functions that do not exist and uses a `sys.path`
>    hack that loads every backend module twice under two names. The working import lines are
>    in `LOCAL_INTEGRATION_CONTRACT.md` §1D.
>
> Everything else — the mandate, the file layout, the issue register, the update rules — applies
> as written. `LOCAL_INTEGRATION_CONTRACT.md` is the live source of truth; this file is the protocol.

---

# SHARED INTEGRATION & LOCAL LINKING PROTOCOL
## Instructions for Agent 1 (Backend Engine) & Agent 2 (Frontend / Claude MCP Planner)

This document governs how both Agents communicate, link code locally **without relying on Git merge steps**, and track issues, edge cases, and future enhancements in real time.

---

## 1. Primary Mandate: The Shared Integration Contract File

Both Agents must immediately collaborate to create and maintain a single source of truth at the project root:
📁 **`LOCAL_INTEGRATION_CONTRACT.md`**

This file acts as the local bridge between both codebases. It defines the API schemas, tracks local file links, logs known issues, and records feature requests across phases.

---

## 2. Local Linking Instructions (Without Git Merges)

To allow the Streamlit UI (Agent 2) to execute the Python data pipeline (Agent 1) locally on the same machine before any Git merge occurs, follow this module import strategy:

### A. Directory Structure Convention
Ensure the root directory contains explicit package initialization files:
```text
my-data-platform/
├── LOCAL_INTEGRATION_CONTRACT.md
├── src/
│   ├── __init__.py
│   ├── backend/
│   │   ├── __init__.py
│   │   ├── profiler.py
│   │   ├── cleaner.py
│   │   └── analytics.py
│   └── frontend/
│       ├── __init__.py
│       ├── mcp_planner.py
│       ├── charts.py
│       └── components.py
└── app.py

```

### B. Python Path Setup (`app.py`)

Agent 2 must configure `app.py` at the root directory to dynamically resolve imports from `src.backend` locally:

```python
import sys
from pathlib import Path

# Dynamically add src directory to Python Path for seamless local linkage
sys.path.append(str(Path(__file__).parent / "src"))

# Local Module Imports linking Agent 1 and Agent 2 codebases
from backend.profiler import profile_dataset
from backend.cleaner import apply_transformations
from backend.analytics import execute_duckdb_query
from frontend.mcp_planner import generate_claude_plan

```

---

## 3. Mandatory Structure of `LOCAL_INTEGRATION_CONTRACT.md`

Both Agents must maintain `LOCAL_INTEGRATION_CONTRACT.md` using the exact layout below:

```markdown
# LOCAL INTEGRATION CONTRACT & ISSUE LOG

## 1. Shared Data Contracts

### A. Profiler Output Schema (Agent 1 -> Agent 2)
```json
{
  "file_name": "string",
  "total_rows": "int",
  "total_columns": "int",
  "columns": [
    {
      "name": "string",
      "data_type": "string",
      "null_count": "int",
      "unique_count": "int",
      "sample_values": ["list of strings (anonymized)"],
      "issues_flagged": ["list of strings"]
    }
  ]
}

```

### B. Claude Execution Plan Schema (Agent 2 -> Agent 1)

```json
{
  "plan_id": "string",
  "summary": "string",
  "recommended_actions": [
    {
      "action_id": "string",
      "operation": "trim_whitespace | drop_exact_duplicates | standardize_dates | fill_nulls",
      "target_column": "string",
      "confidence_score": "float (0.0 to 1.0)",
      "requires_hitl_review": "boolean",
      "explanation": "string"
    }
  ]
}

```

---

## 2. Active Issues & Edge Cases Register

| ID | Originating Agent | Description | Status | Target Resolution |
| --- | --- | --- | --- | --- |
| ISS-001 | Agent 1 | Large CSV memory spikes when parsing mixed date strings | OPEN | Add chunking to Polars reader |
| ISS-002 | Agent 2 | Streamlit re-renders trigger duplicate Claude API calls | OPEN | Wrap planner call in `st.cache_data` |

---

## 3. What to Look Out For (Integration Risks)

* **PII Leakage:** Ensure `profiler.py` anonymizes or strips free-text string samples before feeding the dictionary to `mcp_planner.py`.
* **State Mismatch:** Verify `st.session_state` keys in `app.py` match the data frames returned by `cleaner.py`.
* **DuckDB Type Casting:** Watch out for DuckDB implicit type conversions when querying Polars DataFrames locally.

---

## 4. Backlog: Future Enhancements & Additions

* [ ] Add support for Multi-sheet Excel workbooks (`.xlsx`).
* [ ] Implement local rule memory in SQLite to persist approved HITL decisions.
* [ ] Export HTML/PDF Quality Assessment Audit reports.

```

---

## 4. Agent Rules for Updating the Contract File

1. **Before Starting a Phase:** Read `LOCAL_INTEGRATION_CONTRACT.md` to confirm the JSON interfaces have not changed.
2. **When Changing a Schema:** If Agent 1 or Agent 2 modifies a dictionary key or function argument signature, it MUST update Section 1 of `LOCAL_INTEGRATION_CONTRACT.md` first and log the change.
3. **When Encountering a Bug:** If Agent 1 discovers a UI bug, or Agent 2 encounters a backend error, log it immediately in **Section 2 (Active Issues & Edge Cases Register)**.
4. **Proposing Enhancements:** Add non-critical feature ideas to **Section 4 (Backlog)** instead of writing out-of-scope code.
