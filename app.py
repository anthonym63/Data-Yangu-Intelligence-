"""AI Data Operations Platform for SMEs — Streamlit entrypoint.

Autonomous when safe. Human when necessary. Explainable always.

Run from the project root: ``streamlit run app.py``. That puts the root on
``sys.path``; nothing else is needed and nothing else is done - a second import
root would load every backend class twice (contract §1D).

Streamlit re-executes this file top to bottom on every interaction, so all
session state is declared once in ``_init_state()`` before any widget exists.
"""

from __future__ import annotations

import hashlib

import streamlit as st

from src.backend.analytics import AnalyticsError, aggregate, numeric_summary, timeseries
from src.backend.audit_logger import AuditLog, RollbackError
from src.backend.cleaner import CleaningError, run as run_action
from src.backend.profiler import DatasetLoadError, load_dataset, profile_dataframe
from src.frontend import charts
from src.frontend.autonomy import STRICT, effective_tier
from src.frontend.components import review_card, sidebar, upload
from src.frontend.mcp_planner import (
    PlannerError,
    PrivacyViolationError,
    generate_execution_plan,
    profile_fingerprint,
)

st.set_page_config(page_title="Data Operations Assistant", page_icon="📊", layout="wide")


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #


def _init_state() -> None:
    defaults = {
        "log": None,               # AuditLog - owns original + current frame
        "profile": None,           # DataProfile dict (Interface 1)
        "plan": None,              # ExecutionPlan (frontend envelope)
        "plan_fingerprint": None,  # sha256 of the sanitized profile the plan was made from
        "decisions": {},           # action_id -> pending | approved | rejected
        "applied": set(),          # action_ids currently applied in the log
        "autonomy_mode": STRICT,
        "uploaded_name": None,
        "upload_digest": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _reset_dataset_state() -> None:
    for key in ("log", "profile", "plan", "plan_fingerprint"):
        st.session_state[key] = None
    st.session_state.decisions = {}
    st.session_state.applied = set()


def _strict() -> bool:
    return st.session_state.autonomy_mode == STRICT


# --------------------------------------------------------------------------- #
# Dataset tab
# --------------------------------------------------------------------------- #


def _ingest(uploaded) -> None:
    raw = uploaded.getvalue()
    digest = hashlib.sha256(raw).hexdigest()
    if digest == st.session_state.upload_digest:
        return  # same bytes, same session - nothing to redo
    try:
        with st.spinner("Profiling your data locally…"):
            df = load_dataset(raw, filename=uploaded.name)
            profile = profile_dataframe(df)
    except DatasetLoadError as exc:
        st.error(f"Could not read that file: {exc}", icon="📄")
        return
    _reset_dataset_state()
    st.session_state.log = AuditLog(df, source_name=uploaded.name)
    st.session_state.profile = profile
    st.session_state.uploaded_name = uploaded.name
    st.session_state.upload_digest = digest


def _generate_plan() -> None:
    profile = st.session_state.profile
    fingerprint = profile_fingerprint(profile)
    # ISS-007: a rerun must not re-call Claude for a profile that already has a plan.
    if st.session_state.plan is not None and st.session_state.plan_fingerprint == fingerprint:
        st.info("A plan already exists for this data. Open the Review Queue tab.", icon="🧭")
        return
    try:
        with st.spinner("Claude is reviewing the data profile…"):
            plan = generate_execution_plan(profile)
    except PrivacyViolationError as exc:
        st.error(f"Blocked before sending anything: {exc}", icon="🔒")
        return
    except PlannerError as exc:
        st.error(f"Could not produce a plan: {exc}", icon="⚠️")
        return
    st.session_state.plan = plan
    st.session_state.plan_fingerprint = fingerprint
    st.session_state.decisions = {}
    _auto_apply()
    st.success(f"Plan ready: {len(plan.actions)} proposed step(s). Open the Review Queue tab.", icon="✅")


def _auto_apply() -> None:
    """Autonomous when safe: apply every ``automatic`` action immediately and
    log it. In strict mode nothing qualifies, so nothing happens."""
    if _strict():
        return
    log: AuditLog = st.session_state.log
    for action in st.session_state.plan.actions_as_contract():
        if effective_tier(action, strict=False) != "automatic":
            continue
        try:
            log.apply(action, human_decision="approved")
        except CleaningError:
            continue  # surfaced on its card in the Review Queue
        st.session_state.applied.add(action["action_id"])
        st.session_state.decisions[action["action_id"]] = "approved"


def _render_dataset_tab() -> None:
    st.subheader("1. Upload your data")
    uploaded = upload.render()
    if uploaded is not None:
        _ingest(uploaded)

    profile = st.session_state.profile
    if profile is None:
        st.info("Upload a CSV or Excel file to begin.", icon="📄")
        return

    st.subheader("2. What we found")
    st.dataframe(
        [
            {
                "Column": c["name"],
                "Type": c["dtype"],
                "Missing": c["null_count"],
                "Missing %": c["null_pct"],
                "Unique": c["unique_count"],
                "Issues": ", ".join(i.replace("_", " ") for i in c["issues_detected"]) or "—",
            }
            for c in profile["columns"]
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("3. Ask Claude for a plan")
    st.caption(
        "Only column names, types and summary statistics are sent. "
        "None of your records leave this machine."
    )
    if st.button("Generate cleaning plan", type="primary"):
        _generate_plan()


# --------------------------------------------------------------------------- #
# Review Queue tab
# --------------------------------------------------------------------------- #


def _preview_frames(log: AuditLog, action: dict):
    """Before/after slices for the card. Local only - never leaves the screen."""
    current = log.current
    after, delta = run_action(current, action)
    cols = action.get("target_columns") or list(current.columns[:4])
    after_cols = [c for c in after.columns if c in cols or c.endswith("_outlier_flag")]
    return current.select(cols).head(5), after.select(after_cols).head(5), delta


def _render_review_tab() -> None:
    plan = st.session_state.plan
    log: AuditLog | None = st.session_state.log
    if plan is None or log is None:
        st.info("Generate a plan on the Dataset tab first.", icon="🧭")
        return

    st.subheader("Proposed changes")
    st.markdown(plan.dataset_summary)
    if not plan.actions:
        st.success("No cleaning needed — this dataset already looks healthy.", icon="✨")
        return

    strict = _strict()
    for action in plan.actions_as_contract():
        aid = action["action_id"]
        tier = effective_tier(action, strict=strict)
        applied = aid in st.session_state.applied
        current = st.session_state.decisions.get(aid, "pending")

        if applied or current == "rejected":
            decision = review_card.render(action, tier, applied=applied, decision=current)
        else:
            try:
                before, after, delta = _preview_frames(log, action)
                decision = review_card.render(action, tier, before=before, after=after, delta=delta, decision=current)
            except CleaningError as exc:
                decision = review_card.render(action, tier, error=str(exc), decision=current)

        if decision == "approved" and not applied and tier != "human_required":
            try:
                log.apply(action, human_decision="approved")
                st.session_state.applied.add(aid)
            except CleaningError as exc:
                st.error(f"Could not apply: {exc}", icon="⚠️")
                decision = "rejected"
        st.session_state.decisions[aid] = decision

    st.divider()
    applied_n = len(st.session_state.applied)
    blocked_n = sum(1 for a in plan.actions_as_contract() if effective_tier(a, strict=strict) == "human_required")
    st.caption(f"{applied_n} of {len(plan.actions)} steps applied" + (f" · {blocked_n} blocked as high-risk" if blocked_n else ""))
    _render_downloads(log, "review")


def _render_downloads(log: AuditLog, key_prefix: str) -> None:
    """Rendered on two tabs with identical parameters; explicit keys keep
    Streamlit's auto-generated element ids from colliding."""
    left, middle, right = st.columns(3)
    left.download_button(
        "Download cleaned CSV", log.current.write_csv().encode("utf-8"),
        f"cleaned_{st.session_state.uploaded_name or 'data.csv'}", "text/csv", key=f"{key_prefix}_download_csv", use_container_width=True,
    )
    middle.download_button(
        "Change log (JSON)", log.export("json"), "change_log.json", "application/json", key=f"{key_prefix}_download_json", use_container_width=True,
    )
    right.download_button(
        "Change log (HTML)", log.export("html"), "change_log.html", "text/html", key=f"{key_prefix}_download_html", use_container_width=True,
    )


# --------------------------------------------------------------------------- #
# Insights tab
# --------------------------------------------------------------------------- #


def _pick_columns(profile: dict, df):
    """Heuristic defaults until Phase 2 wires questions to SQL: the first
    low-cardinality text column as a dimension, the first numeric measure."""
    dimension = next(
        (c["name"] for c in profile["columns"]
         if not c["is_numeric"] and 1 < c["unique_count"] <= 20 and c["name"] in df.columns),
        None,
    )
    measure = next((c["name"] for c in profile["columns"] if "numeric" in c and c["name"] in df.columns), None)
    date_col = next((c["name"] for c in profile["columns"] if "mixed_date_formats" in c["issues_detected"]), None)
    return dimension, measure, date_col


def _render_insights_tab() -> None:
    plan = st.session_state.plan
    log: AuditLog | None = st.session_state.log
    profile = st.session_state.profile
    if plan is None or log is None:
        st.info("Generate a plan on the Dataset tab first.", icon="🧭")
        return

    if plan.analysis_questions:
        st.subheader("Questions worth asking")
        for q in plan.analysis_questions:
            st.markdown(f"- {q}")
        st.caption("Answering these directly is a Phase 2 feature. The charts below use the cleaned data as it stands.")

    df = log.current
    dimension, measure, date_col = _pick_columns(profile, df)
    if measure is None:
        st.info("No numeric measure column found to chart.", icon="📉")
        return

    try:
        summary = numeric_summary(df, measure).row(0, named=True)
        tiles = st.columns(4)
        tiles[0].metric(f"{measure} · total rows", f"{summary['count']:,}")
        tiles[1].metric("median", f"{summary['median']:,.2f}")
        tiles[2].metric("mean", f"{summary['mean']:,.2f}")
        tiles[3].metric("max", f"{summary['max']:,.2f}")
    except AnalyticsError as exc:
        st.warning(f"Summary unavailable: {exc}")

    if dimension:
        try:
            rows = aggregate(df, dimension, measure, "sum").to_dicts()
            st.plotly_chart(charts.bar_chart(rows, dimension, "value", f"Total {measure} by {dimension}"), key="insights_bar",
                            use_container_width=True)
        except AnalyticsError as exc:
            st.warning(f"Chart unavailable: {exc}")

    if date_col and date_col in df.columns:
        try:
            rows = timeseries(df, date_col, measure, grain="month").to_dicts()
            if rows:
                st.plotly_chart(charts.line_chart(rows, "period", "value", f"Monthly {measure}"), key="insights_trend",
                                use_container_width=True)
        except AnalyticsError as exc:
            st.warning(f"Trend unavailable: {exc}")


# --------------------------------------------------------------------------- #
# Audit Trail tab
# --------------------------------------------------------------------------- #


def _render_audit_tab() -> None:
    log: AuditLog | None = st.session_state.log
    if log is None:
        st.info("Upload a file to start an audit trail.", icon="📜")
        return

    st.subheader("Every change, in order")
    st.caption(f"{log.original.height:,} rows originally · {log.current.height:,} now · {len(log)} step(s) applied")

    if not log.entries:
        st.info("No transformations applied yet.", icon="✨")
        return

    for i, entry in enumerate(log.entries, start=1):
        with st.container(border=True):
            head, undo = st.columns([5, 1])
            head.markdown(f"**{i}. {entry['operation']}** — {entry['summary']}")
            head.caption(
                f"{entry['timestamp']} · rows {entry['rows_before']:,} → {entry['rows_after']:,} · "
                f"{entry['cells_changed']:,} cells changed · decision: {entry['human_decision']}"
            )
            if undo.button("Undo from here", key=f"undo_{entry['action_id']}", use_container_width=True):
                _rollback(entry["action_id"])
                st.rerun()

    st.divider()
    _render_downloads(log, "audit")


def _rollback(action_id: str) -> None:
    log: AuditLog = st.session_state.log
    entries = log.entries
    idx = next((i for i, e in enumerate(entries) if e["action_id"] == action_id), None)
    if idx is None:
        return
    undone = [e["action_id"] for e in entries[idx:]]
    try:
        log.rollback(action_id)
    except RollbackError:
        return
    for aid in undone:
        st.session_state.applied.discard(aid)
        st.session_state.decisions[aid] = "pending"


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main() -> None:
    _init_state()
    st.title("📊 Data Operations Assistant")
    st.caption("Autonomous when safe. Human when necessary. Explainable always.")

    sidebar.render(st.session_state.profile, st.session_state.uploaded_name)

    dataset, review, insights, audit = st.tabs(["Dataset", "Review Queue", "Insights", "Audit Trail"])
    with dataset:
        _render_dataset_tab()
    with review:
        _render_review_tab()
    with insights:
        _render_insights_tab()
    with audit:
        _render_audit_tab()


main()
