# AI Data Operations & Analyst Platform for SMEs
## Detailed Development Proposal – Privacy-First Hybrid Autonomy

**Version:** 1.0  
**Status:** MVP Development Plan & Architecture Blueprint  
**Concurrency Target:** 2 Developers (4-Hour Execution Window)

---

## 1. The Whole Idea

Build a practical AI-powered data operations and analyst platform designed specifically for small and medium enterprises (SMEs).

The platform ingests messy business data (Excel/CSV files, exports from sales or accounting tools), profiles and cleans it, validates data quality, investigates anomalies, surfaces clear insights, and produces usable reports and visualizations. Critically, it operates with **controlled autonomy**:

- It performs safe, low-risk operations automatically.
- It pauses and asks a human whenever confidence is low or business impact is high.
- Every step is explainable and reversible.

**Core product principle:**  
**Autonomous when safe. Human when necessary. Explainable always.**

The AI never needs to “see” or store raw customer, financial, or operational records. It works solely with high-level summaries, data profiles, and results of deterministic python operations. All actual data transformations happen under local execution. This approach delivers deep intelligence while maintaining strict privacy by design.

---

## 2. Problems Being Solved

Most SMEs face chronic data challenges that block reliable AI or BI use:

- **Messy, fragmented data:** Duplicates, inconsistent formats (dates, phones, names), missing values, and siloed spreadsheets consume hours of manual cleaning.
- **Lack of expertise and trust:** No dedicated data team. standard AI tools produce plausible but hallucinated or ungrounded insights. SMEs have thin safety nets—errors in revenue or inventory hurt immediately.
- **One-off vs continuous:** Tools clean a file once; real businesses need ongoing monitoring and scheduled insights as new data arrives.
- **Governance and risk:** High-impact actions (deleting records, altering financials) require human oversight. Full autonomy is dangerous; pure manual work is too slow.
- **Privacy and cost:** Cloud-only tools raise severe privacy concerns for sensitive customer or financial data. Enterprise platforms (Snowflake, Databricks) are overly complex and cost-prohibitive.

---

## 3. What Needs to Be Done (High-Level)

To build a complete hybrid analyst platform, the core engine must accomplish the following lifecycle:

**Ingest → Understand → Clean → Validate → Analyze → Visualize → Explain → Verify → Publish → Monitor**

1. **User Experience:** Calm, plain-language assistant interface showing profile summaries and side-by-side proposed transformations.
2. **Privacy Engine:** Architecture that isolates LLM planners from raw data rows using anonymized metadata summaries.
3. **Autonomy Matrix:** Action-level permissions combining confidence levels with business-impact scores.
4. **Reversibility & Provenance:** Full audit log tracking every operation back to original files with single-click rollbacks.
5. **Organizational Memory:** Capturing approved/rejected transformations to refine future confidence scoring automatically.

---

## 4. Viable MVP Scope (4-Hour Build)

### In Scope for MVP
- Multi-file upload for **Excel (.xlsx)** and **CSV (.csv)** datasets.
- Deterministic data profiling (column types, missingness, anomaly detection, unique counts) computed locally.
- Rule & heuristic cleaning pipeline (exact deduplication, whitespace trim, date standardization, null imputation).
- Side-by-side Human-in-the-Loop (HITL) queue for uncertain transformations.
- Conversational analytics & chart generation executed via DuckDB / Polars.
- Full audit log with downloadable cleaned datasets and HTML execution reports.
- Action-level autonomy config toggle (Strict Human Review vs Semi-Autonomous).

### Explicitly Out of Scope for MVP
- Direct database / API connectors (SQL Server, QuickBooks, Salesforce).
- Multi-tenant auth or complex RBAC roles.
- Continuous scheduled background jobs or live webhook alerts.
- Complex graph-based cross-file entity resolution.

---

## 5. Detailed Development Proposal

### 5.1 Privacy-First Processing Model
+-------------------------------------------------------------------+
|                           LOCAL RUNTIME                           |
|  [Raw Data Files] ---> ( DuckDB / Polars Engine )                |
|                                |                                  |
|                        Extract Metadata Only                      |
|                                v                                  |
|                { Schema, Column Stats, Samples }                  |
+-------------------------------------------------------------------+
|
Sanitized JSON
v
+-------------------------------------------------------------------+
|                        REMOTE LLM PLANNER                         |
|  Analyzes metadata -> Generates MCP Execution Plan / Code Spec    |
+-------------------------------------------------------------------+
|
Execution Plan JSON
v
+-------------------------------------------------------------------+
|                           LOCAL RUNTIME                           |
|  Validates Safety -> Applies Transformation -> Logs Revisions     |
+-------------------------------------------------------------------+


### 5.2 Autonomy Policy Matrix

| Confidence Level | Low Business Impact | High Business Impact |
| :--- | :--- | :--- |
| **High Confidence (>90%)** | **Auto-Approve**: Executed immediately, logged in audit trail. | **Review Required**: Proposed with diff preview for human confirmation. |
| **Low Confidence (<90%)** | **Review Required**: Flagged in HITL queue with suggested options. | **Human Required**: Stopped; requires explicit human manual entry. |

---

## 6. Phased Roadmap

- **Phase 0 (MVP - Day 1):** Single-session Excel/CSV ingestion, automated profiling, rule-based cleaning with review queues, DuckDB analytics, interactive charts, and downloadable change logs.
- **Phase 1 (Post-MVP):** Local persistence layer for organizational rules memory, batch multi-file processing, exportable PDF executive briefings.
- **Phase 2 (Scale & Integrations):** PostgreSQL / Snowflake connectors, API webhook triggers, conti