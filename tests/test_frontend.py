"""Agent 2 test suite: privacy guardrail, contract mirroring, planner behaviour,
autonomy toggle, charts. Zero API calls - every planner test uses ``StubClient``.
"""

from __future__ import annotations

import copy
import json
import uuid
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src import contracts
from src.backend.profiler import profile_dataframe
from src.frontend.autonomy import apply_strict_mode, effective_tier
from src.frontend.charts import bar_chart, line_chart, scatter_chart
from src.frontend.mcp_planner import (
    PlannerError,
    PrivacyViolationError,
    generate_execution_plan,
    profile_fingerprint,
    sanitize_profile,
)
from src.frontend.prompts import PROPOSE_EXECUTION_PLAN_TOOL
from src.frontend.schemas import ColumnProfile, DataProfile, ExecutionPlan, PlannedAction, ProposedAction
from tests.conftest import CANARIES, FIXTURE_PLAN, StubClient, TextBlock, ToolUseBlock, make_response, planned

# --------------------------------------------------------------------------- #
# Privacy guardrail - the release-blocking tests
# --------------------------------------------------------------------------- #


def test_sanitize_profile_strips_unknown_keys(profile):
    """Unrecognised metadata is dropped silently by allowlist reconstruction."""
    profile["profiling_duration_ms"] = 412
    profile["columns"][0]["engine_version"] = "1.44"

    payload = sanitize_profile(profile)

    assert "profiling_duration_ms" not in payload
    assert "engine_version" not in payload["columns"][0]
    assert set(payload) == {"total_rows", "total_columns", "exact_duplicate_count", "columns"}


def test_sanitize_profile_drops_profiled_at(profile):
    """The one non-deterministic field would defeat prompt caching (contract §1A)."""
    assert "profiled_at" in profile
    assert "profiled_at" not in sanitize_profile(profile)


def test_sanitize_profile_raises_on_raw_rows(profile):
    """A key naming row data fails loudly, and no client call is made."""
    profile["sample_rows"] = [["Alice Wanjiku", 4500.0]]
    client = StubClient()

    with pytest.raises(PrivacyViolationError, match="sample_rows"):
        generate_execution_plan(profile, client=client)

    assert client.calls == [], "raised after reaching the API instead of before"


@pytest.mark.parametrize("key", ["sample_values", "top_values", "mode", "examples", "head"])
def test_sanitize_profile_raises_on_nested_raw_keys(profile, key):
    """The scan is recursive and covers every name the contract forbids."""
    profile["columns"][2][key] = ["Alice Wanjiku"]
    with pytest.raises(PrivacyViolationError, match=key):
        sanitize_profile(profile)


def test_numeric_block_passes_through_allowlisted_aggregates_only(profile):
    col = next(c for c in profile["columns"] if c["name"] == "amount_kes")
    col["numeric"]["p99"] = 890000.0  # not in the contract -> dropped, not sent

    clean = next(c for c in sanitize_profile(profile)["columns"] if c["name"] == "amount_kes")

    assert set(clean["numeric"]) == {"min", "max", "mean", "median", "std"}


def test_payload_contains_no_canary_values(canary_df):
    """RELEASE BLOCKER: the exact string sent to Anthropic carries no cell value."""
    client = StubClient([make_response([ToolUseBlock(copy.deepcopy(FIXTURE_PLAN) | {"actions": []})])])

    generate_execution_plan(profile_dataframe(canary_df), client=client)

    sent = client.sent_payload()
    for canary in CANARIES:
        assert canary not in sent, f"raw value {canary!r} leaked into the payload"
    assert "profiled_at" not in sent


def test_sanitize_accepts_validated_pydantic_model(profile):
    model = DataProfile.model_validate(profile)
    payload = sanitize_profile(model)
    assert payload["total_rows"] == 24
    assert len(payload["columns"]) == 9


def test_profile_model_forbids_extra_fields(profile):
    """First interlock: the Pydantic mirror rejects unknown fields outright."""
    profile["sample_rows"] = [["x"]]
    with pytest.raises(ValidationError):
        DataProfile.model_validate(profile)


def test_profile_fingerprint_is_stable_across_timestamps(profile):
    a = profile_fingerprint(profile)
    profile["profiled_at"] = "2099-01-01T00:00:00+00:00"
    assert profile_fingerprint(profile) == a


# --------------------------------------------------------------------------- #
# Contract mirroring - Pydantic must equal the TypedDicts key-for-key
# --------------------------------------------------------------------------- #


def _typeddict_keys(td) -> set[str]:
    return set(td.__required_keys__) | set(td.__optional_keys__)


@pytest.mark.parametrize(
    ("pydantic_model", "typeddict"),
    [
        (ColumnProfile, contracts.ColumnProfile),
        (DataProfile, contracts.DataProfile),
        (ProposedAction, contracts.ProposedAction),
    ],
    ids=["ColumnProfile", "DataProfile", "ProposedAction"],
)
def test_pydantic_mirrors_typeddict_contracts(pydantic_model, typeddict):
    assert set(pydantic_model.model_fields) == _typeddict_keys(typeddict)


def test_real_profile_validates_against_pydantic_mirror(profile):
    DataProfile.model_validate(profile)


def test_tool_schema_operation_enum_matches_contract():
    enum = PROPOSE_EXECUTION_PLAN_TOOL["input_schema"]["properties"]["actions"]["items"]["properties"]["operation"]["enum"]
    assert set(enum) == contracts.CLEANING_OPERATIONS
    assert enum == sorted(enum), "sorted so the cached prompt prefix is byte-stable"


def test_tool_schema_is_json_serialisable_and_strict():
    json.dumps(PROPOSE_EXECUTION_PLAN_TOOL)
    assert PROPOSE_EXECUTION_PLAN_TOOL["strict"] is True
    assert PROPOSE_EXECUTION_PLAN_TOOL["input_schema"]["additionalProperties"] is False


# --------------------------------------------------------------------------- #
# Schema validation
# --------------------------------------------------------------------------- #


def test_planned_action_rejects_unknown_operation():
    with pytest.raises(ValidationError, match="unknown operation"):
        PlannedAction.model_validate(planned("delete_all_rows", ["x"], 0.9, "low"))


@pytest.mark.parametrize("confidence", [1.4, -0.1])
def test_planned_action_rejects_confidence_out_of_range(confidence):
    with pytest.raises(ValidationError):
        PlannedAction.model_validate(planned("trim_whitespace", ["x"], confidence, "low"))


def test_planned_action_rejects_extra_fields():
    with pytest.raises(ValidationError):
        PlannedAction.model_validate(planned("trim_whitespace", ["x"], 0.9, "low") | {"sql": "DROP TABLE"})


def test_planned_action_requires_targets_except_whole_row_dedup():
    PlannedAction.model_validate(planned("drop_exact_duplicates", [], 0.99, "low"))
    with pytest.raises(ValidationError, match="target column"):
        PlannedAction.model_validate(planned("trim_whitespace", [], 0.99, "low"))


def test_column_profile_rejects_unknown_issue_code():
    with pytest.raises(ValidationError, match="unknown issue code"):
        ColumnProfile(name="x", dtype="String", null_count=0, null_pct=0.0, unique_count=1,
                      is_numeric=False, issues_detected=["outlier_values"])


# --------------------------------------------------------------------------- #
# Planner behaviour
# --------------------------------------------------------------------------- #


def test_planner_returns_validated_plan(profile, stub_client):
    plan = generate_execution_plan(profile, client=stub_client)

    assert isinstance(plan, ExecutionPlan)
    assert len(plan.actions) == 5
    assert plan.analysis_questions == FIXTURE_PLAN["analysis_questions"]


def test_ids_are_minted_locally_and_unique(profile, stub_client):
    """Never trust the model for an id the audit trail depends on."""
    plan = generate_execution_plan(profile, client=stub_client)

    ids = [a.action_id for a in plan.actions]
    assert len(set(ids)) == len(ids)
    for value in ids + [plan.plan_id]:
        assert uuid.UUID(hex=value).version == 4
    assert "action_id" not in json.dumps(FIXTURE_PLAN) and "plan_id" not in json.dumps(FIXTURE_PLAN)


def test_tiers_are_derived_by_backend_policy(profile, stub_client):
    plan = generate_execution_plan(profile, client=stub_client)
    by_op = {a.operation: a for a in plan.actions}

    assert by_op["drop_exact_duplicates"].autonomy_tier == "automatic"
    assert by_op["trim_whitespace"].requires_review is False
    assert by_op["impute_nulls"].autonomy_tier == "human_required"  # 0.42 x high
    assert by_op["impute_nulls"].requires_review is True


def test_planner_sends_expected_request_shape(profile, stub_client):
    generate_execution_plan(profile, client=stub_client)
    call = stub_client.calls[0]

    assert call["model"] == "claude-opus-5"
    assert call["thinking"] == {"type": "adaptive"}
    assert "budget_tokens" not in json.dumps(call["thinking"])
    assert call["tool_choice"] == {"type": "auto"}
    assert call["tools"][0]["name"] == "propose_execution_plan"
    assert call["tools"][0]["strict"] is True
    assert call["fallbacks"] == "default"
    sent = json.loads(stub_client.sent_payload())
    assert set(sent) == {"total_rows", "total_columns", "exact_duplicate_count", "columns"}


def test_planner_handles_refusal_stop_reason(profile):
    client = StubClient([make_response([], stop_reason="refusal", explanation="policy")])
    with pytest.raises(PlannerError, match="declined"):
        generate_execution_plan(profile, client=client)


def test_planner_retries_once_when_no_tool_use(profile):
    client = StubClient([make_response([TextBlock()]), make_response([ToolUseBlock(copy.deepcopy(FIXTURE_PLAN))])])
    plan = generate_execution_plan(profile, client=client)
    assert len(client.calls) == 2 and len(plan.actions) == 5


def test_planner_raises_after_exhausting_attempts(profile):
    client = StubClient([make_response([TextBlock()]), make_response([TextBlock()])])
    with pytest.raises(PlannerError, match="did not call propose_execution_plan"):
        generate_execution_plan(profile, client=client)
    assert len(client.calls) == 2


def test_planner_rejects_invalid_plan_wholesale(profile):
    """One bad action invalidates the plan; partial execution is not an option."""
    broken = copy.deepcopy(FIXTURE_PLAN)
    broken["actions"][1]["operation"] = "delete_all_rows"
    client = StubClient([make_response([ToolUseBlock(broken)])])
    with pytest.raises(PlannerError, match="failed validation"):
        generate_execution_plan(profile, client=client)


def test_planner_rejects_model_supplied_permission(profile):
    """If the model tries to hand itself a tier, the strict schema rejects it."""
    sneaky = copy.deepcopy(FIXTURE_PLAN)
    sneaky["actions"][4]["autonomy_tier"] = "automatic"
    client = StubClient([make_response([ToolUseBlock(sneaky)])])
    with pytest.raises(PlannerError, match="failed validation"):
        generate_execution_plan(profile, client=client)


def test_planner_accepts_empty_plan(profile):
    """Clean data yields no actions, and that is a success, not an error."""
    empty = {"dataset_summary": "Already clean.", "actions": [], "analysis_questions": []}
    client = StubClient([make_response([ToolUseBlock(empty)])])
    plan = generate_execution_plan(profile, client=client)
    assert plan.actions == []


# --------------------------------------------------------------------------- #
# Autonomy toggle (the matrix itself is tested in tests/test_policy.py)
# --------------------------------------------------------------------------- #


def test_strict_mode_demotes_automatic_only():
    assert apply_strict_mode("automatic", True) == "review_required"
    assert apply_strict_mode("review_required", True) == "review_required"
    assert apply_strict_mode("human_required", True) == "human_required"
    assert apply_strict_mode("automatic", False) == "automatic"


def test_effective_tier_ignores_incoming_tier():
    action = planned("impute_nulls", ["amount_kes"], 0.3, "high") | {
        "action_id": "x", "autonomy_tier": "automatic", "requires_review": False,
    }
    assert effective_tier(action, strict=False) == "human_required"


# --------------------------------------------------------------------------- #
# Charts
# --------------------------------------------------------------------------- #

ROWS = [{"region": "North", "value": 41_200.0}, {"region": "South", "value": 88_500.0}, {"region": "East", "value": 12_050.0}]


@pytest.mark.parametrize("builder", [bar_chart, line_chart, scatter_chart])
def test_charts_builder_returns_figure(builder):
    import plotly.graph_objects as go

    figure = builder(ROWS, "region", "value", "Revenue by region")
    assert isinstance(figure, go.Figure)
    assert figure.layout.title.text == "Revenue by region"


def test_bar_chart_sorts_descending():
    assert list(bar_chart(ROWS, "region", "value").data[0].x) == ["South", "North", "East"]


def test_chart_raises_on_missing_column():
    with pytest.raises(KeyError, match="profit"):
        bar_chart(ROWS, "region", "profit")


# --------------------------------------------------------------------------- #
# SDK failure translation - the UI must see one PlannerError, never a traceback
# --------------------------------------------------------------------------- #


class _RaisingClient:
    """Stands in for an Anthropic client whose request fails."""

    def __init__(self, exc: Exception):
        self._exc = exc
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        raise self._exc


def test_planner_translates_missing_credentials(profile):
    """The SDK's TypeError for an unset key becomes a plain instruction, not a stack trace."""
    client = _RaisingClient(TypeError(
        "Could not resolve authentication method. Expected one of api_key, auth_token, or credentials to be set."
    ))
    with pytest.raises(PlannerError, match="No Anthropic credentials found"):
        generate_execution_plan(profile, client=client)


def test_planner_translates_unexpected_sdk_error(profile):
    client = _RaisingClient(RuntimeError("socket closed"))
    with pytest.raises(PlannerError, match="Unexpected error calling Claude"):
        generate_execution_plan(profile, client=client)
