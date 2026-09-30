"""End-to-end integration: the two codebases running as one, offline.

    load -> profile -> plan (stubbed Claude) -> policy -> apply via AuditLog
         -> audit trail -> DuckDB analytics -> Plotly figure -> export -> rollback

plus the two properties the whole product rests on: nothing row-level reaches
the (stubbed) API, and a full rollback is byte-equal to the original.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import plotly.graph_objects as go
import pytest
from polars.testing import assert_frame_equal

from src.backend import analytics
from src.backend.audit_logger import AuditLog
from src.backend.cleaner import run as run_action
from src.backend.profiler import load_dataset, profile_dataframe
from src.contracts import ISSUE_CODES
from src.frontend import charts
from src.frontend.autonomy import effective_tier
from src.frontend.mcp_planner import generate_execution_plan, profile_fingerprint, sanitize_profile
from tests.conftest import CANARIES, DEMO_CSV, FIXTURE_PLAN, StubClient, ToolUseBlock, make_response


def _stub() -> StubClient:
    return StubClient([make_response([ToolUseBlock(copy.deepcopy(FIXTURE_PLAN))])])


def test_full_pipeline_end_to_end(messy_df, log_capture):
    # 1. Profile - every issue code the platform knows about fires on the fixture.
    profile = profile_dataframe(messy_df)
    fired = {code for c in profile["columns"] for code in c["issues_detected"]}
    assert fired == ISSUE_CODES
    assert profile["exact_duplicate_count"] == 3

    # 2. Plan via (stubbed) Claude. What went over the wire is aggregate-only.
    client = _stub()
    plan = generate_execution_plan(profile, client=client)
    sent = json.loads(client.sent_payload())
    assert set(sent) == {"total_rows", "total_columns", "exact_duplicate_count", "columns"}
    assert all(set(c) <= {"name", "dtype", "null_count", "null_pct", "unique_count",
                          "is_numeric", "issues_detected", "numeric"} for c in sent["columns"])

    # 3. Policy gate - tiers come from the backend, and the financial imputation is blocked.
    tiers = {a["operation"]: effective_tier(a, strict=False) for a in plan.actions_as_contract()}
    assert tiers["drop_exact_duplicates"] == "automatic"
    assert tiers["impute_nulls"] == "human_required"

    # 4. Apply everything a human or the policy allowed, through the audit log.
    log = AuditLog(messy_df, source_name="messy_sales.csv")
    for action in plan.actions_as_contract():
        if effective_tier(action, strict=False) != "human_required":
            log.apply(action, human_decision="approved")

    cleaned = log.current
    assert cleaned.height == 21  # 3 exact duplicates gone
    assert all(n == n.strip() for n in cleaned["customer_name"].drop_nulls().to_list())
    iso = [d for d in cleaned["order_date"].drop_nulls().to_list() if d != "not a date"]
    assert all(len(d) == 10 and d[4] == "-" for d in iso)
    assert set(cleaned["region"].to_list()) <= {"Nairobi", "Mombasa", "Kisumu", "Nakuru", "Eldoret", "Thika"}
    assert cleaned["amount_kes"].null_count() == 1  # the blocked imputation did NOT run

    # 5. Audit trail: one entry per step, one before/after log line per step.
    assert len(log) == 4
    assert [e["operation"] for e in log.entries] == [
        "drop_exact_duplicates", "trim_whitespace", "standardize_dates", "normalize_casing",
    ]
    assert sum(1 for m in log_capture if m.startswith("audit | ") and " -> " in m) == 4

    # 6. Analytics over the cleaned frame -> chart objects.
    by_region = analytics.aggregate(cleaned, "region", "amount_kes", "sum").to_dicts()
    fig = charts.bar_chart(by_region, "region", "value", "Revenue by region")
    assert isinstance(fig, go.Figure) and len(fig.data[0].x) == cleaned["region"].n_unique()

    monthly = analytics.timeseries(cleaned, "order_date", "amount_kes", grain="month").to_dicts()
    assert len(monthly) == 2  # Jan + Feb 2024
    assert isinstance(charts.line_chart(monthly, "period", "value"), go.Figure)

    # 7. Exports are honest.
    exported = json.loads(log.export("json"))
    assert exported["original_rows"] == 24 and exported["current_rows"] == 21 and len(exported["steps"]) == 4
    assert "<table>" in log.export("html")

    # 8. Full rollback is byte-equal to the original upload.
    assert_frame_equal(log.rollback_all(), messy_df)


def test_no_canary_reaches_the_wire_end_to_end(canary_df):
    """The property the product is sold on, checked at the last possible point:
    the exact string handed to the Anthropic client."""
    client = StubClient([make_response([ToolUseBlock({"dataset_summary": "ok", "actions": [], "analysis_questions": []})])])

    generate_execution_plan(profile_dataframe(canary_df), client=client)

    sent = client.sent_payload()
    for canary in CANARIES:
        assert canary not in sent
    # card_number is identifier-like -> no aggregates; amount is a measure -> aggregates present
    cols = {c["name"]: c for c in json.loads(sent)["columns"]}
    assert "numeric" not in cols["card_number"]
    assert set(cols["amount"]["numeric"]) == {"min", "max", "mean", "median", "std"}


def test_preview_matches_apply(messy_df):
    """What the review card shows is exactly what apply will do."""
    log = AuditLog(messy_df)
    action = generate_execution_plan(profile_dataframe(messy_df), client=_stub()).actions_as_contract()[1]

    previewed, delta = run_action(log.current, action)
    log.apply(action)

    assert_frame_equal(previewed, log.current)
    assert delta.cells_changed == 5


def test_strict_mode_blocks_all_auto_apply(messy_df):
    plan = generate_execution_plan(profile_dataframe(messy_df), client=_stub())
    assert all(effective_tier(a, strict=True) != "automatic" for a in plan.actions_as_contract())


def test_demo_sample_flows_through_backend():
    """The 50-row demo file shipped for the UI must load, profile, and clean."""
    df = load_dataset(DEMO_CSV)
    profile = profile_dataframe(df)
    assert profile["total_rows"] == 50 and profile["exact_duplicate_count"] == 3
    fired = {code for c in profile["columns"] for code in c["issues_detected"]}
    assert {"mixed_date_formats", "leading_trailing_whitespace", "inconsistent_casing",
            "high_null_rate", "numeric_outliers", "possible_duplicate_key"} <= fired

    sanitize_profile(profile)  # must not raise: the demo profile is aggregate-only

    log = AuditLog(df, source_name="demo")
    for action in generate_execution_plan(profile, client=StubClient([make_response([ToolUseBlock({
        "dataset_summary": "demo",
        "actions": [
            {"operation": "drop_exact_duplicates", "target_columns": [], "parameters": {},
             "confidence": 0.99, "business_impact": "low", "rationale": "3 duplicate rows."},
            {"operation": "standardize_dates", "target_columns": ["transaction_date"], "parameters": {},
             "confidence": 0.95, "business_impact": "low", "rationale": "Three date formats."},
        ],
        "analysis_questions": [],
    })])])).actions_as_contract():
        log.apply(action)
    assert log.current.height == 47
    assert analytics.timeseries(log.current, "transaction_date", "revenue").height == 2


@pytest.mark.parametrize("path", ["app.py"])
def test_streamlit_app_boots_headless(path):
    """STEP 5: the app must start with no import errors and no session-state bugs."""
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / path), default_timeout=60)
    at.run()

    assert not at.exception, at.exception
    assert at.session_state["autonomy_mode"] == "strict"
    assert at.session_state["plan"] is None
    assert len(at.tabs) == 4


def test_streamlit_app_renders_populated_state(messy_df):
    """Every tab with real data in session state: profile table, review cards
    with previews, two charts, an audit entry, and download buttons on two tabs.

    The empty-state boot cannot see element-id collisions or rendering errors
    that only occur once data is loaded; this seeds the state the app holds
    after an upload, a plan, and one applied step.
    """
    from streamlit.testing.v1 import AppTest

    profile = profile_dataframe(messy_df)
    plan = generate_execution_plan(profile, client=_stub())
    log = AuditLog(messy_df, source_name="messy_sales.csv")
    first = plan.actions_as_contract()[0]
    log.apply(first, human_decision="approved")

    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app.py"), default_timeout=60)
    at.session_state["log"] = log
    at.session_state["profile"] = profile
    at.session_state["plan"] = plan
    at.session_state["plan_fingerprint"] = profile_fingerprint(profile)
    at.session_state["uploaded_name"] = "messy_sales.csv"
    at.session_state["applied"] = {first["action_id"]}
    at.session_state["decisions"] = {first["action_id"]: "approved"}
    at.run()

    assert not at.exception, at.exception
    assert len(at.tabs) == 4
    labels = [b.label for b in at.button]
    # Strict mode: the three unapplied low-impact actions offer approval; the
    # high-impact imputation is blocked and offers nothing.
    assert labels.count("Approve") == 3
    assert "Undo from here" in labels
