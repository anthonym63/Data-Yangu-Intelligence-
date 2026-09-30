"""Claude planning layer: sanitized data profile in, validated execution plan out.

The privacy guarantee lives in this module. A system prompt asking the model not
to look at raw rows accomplishes nothing, because the model never controls what
we send it. ``sanitize_profile`` does, and it runs before the client is touched.

The backend already guarantees a ``DataProfile`` carries no cell value (contract
§1A). This module's job is to make sure the frontend cannot *add* one on the way
out - "keys removed, never keys added" (PHASE_1_BACKEND.md §4).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import ValidationError

from src.backend.policy import annotate

from .prompts import PROPOSE_EXECUTION_PLAN_TOOL, SYSTEM_PROMPT
from .schemas import DataProfile, ExecutionPlan, PlannerOutput, ProposedAction

MODEL = "claude-opus-5"
MAX_TOKENS = 16_000
REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"

#: The only keys permitted to leave this machine. The payload is rebuilt from
#: these sets field by field rather than filtered, so an unknown key cannot
#: survive by default. A denylist would fail open the first time the profiler
#: grows a field nobody remembered to ban.
#:
#: ``profiled_at`` is deliberately absent: it is the one non-deterministic
#: field, and a changing timestamp inside the payload silently defeats prompt
#: caching (contract §1A). Removing keys is permitted; adding them is not.
_PROFILE_ALLOWLIST = frozenset({"total_rows", "total_columns", "exact_duplicate_count", "columns"})
_COLUMN_ALLOWLIST = frozenset(
    {"name", "dtype", "null_count", "null_pct", "unique_count", "is_numeric", "issues_detected", "numeric"}
)
_NUMERIC_ALLOWLIST = frozenset({"min", "max", "mean", "median", "std"})

#: Key names that denote row-level data at any depth. Unknown keys are dropped
#: in silence, but these mean something upstream is trying to hand records to
#: the planner, so they fail loudly instead.
_RAW_DATA_KEYS = frozenset(
    {
        "sample_rows", "samples", "sample_values", "sample", "rows", "head", "tail",
        "values", "examples", "example", "preview", "records", "data", "top_values",
        "mode", "mode_value", "most_common", "most_common_value", "first", "last",
    }
)


class PrivacyViolationError(Exception):
    """Raised when a payload would carry row-level data off the machine.

    Always raised before any network call. A failed request is recoverable; a
    leaked customer record is not.
    """


class PlannerError(Exception):
    """Raised when Claude could not produce a usable plan."""


# --------------------------------------------------------------------------- #
# Privacy guardrail
# --------------------------------------------------------------------------- #


def _scan_for_raw_data_keys(node: Any, path: str = "profile") -> None:
    """Walk a nested structure and reject any key that names row-level data."""
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).lower() in _RAW_DATA_KEYS:
                raise PrivacyViolationError(
                    f"Refusing to build a payload containing raw data: found key "
                    f"{str(key)!r} at {path}. Only aggregate statistics may reach "
                    f"the Claude API."
                )
            _scan_for_raw_data_keys(value, f"{path}.{key}")
    elif isinstance(node, (list, tuple)):
        for index, item in enumerate(node):
            _scan_for_raw_data_keys(item, f"{path}[{index}]")


def _scalar(value: Any, path: str) -> Any:
    if isinstance(value, bool) or isinstance(value, (int, float, str)) or value is None:
        return value
    raise PrivacyViolationError(f"Non-scalar value at {path}: {type(value).__name__}.")


def _clean_numeric(block: Any, path: str) -> dict[str, float]:
    if not isinstance(block, dict):
        raise PrivacyViolationError(f"Expected an aggregates mapping at {path}, got {type(block).__name__}.")
    clean: dict[str, float] = {}
    for key in sorted(_NUMERIC_ALLOWLIST):
        if key not in block:
            continue
        value = block[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise PrivacyViolationError(f"Non-numeric aggregate at {path}.{key}: {type(value).__name__}.")
        clean[key] = float(value)
    return clean


def sanitize_profile(profile: DataProfile | dict) -> dict:
    """Reduce a data profile to the minimum aggregate payload Claude may see.

    Two failure modes, deliberately different in severity:

    * Unrecognised keys such as ``profiling_duration_ms`` are dropped in
      silence - allowlist reconstruction never copies them.
    * Keys naming row-level data such as ``sample_values`` raise
      ``PrivacyViolationError``, because their presence means something
      upstream is trying to send records.

    Raises:
        PrivacyViolationError: before any network call, always.
    """
    raw = profile.model_dump(mode="json", exclude_none=True) if isinstance(profile, DataProfile) else profile
    if not isinstance(raw, dict):
        raise PrivacyViolationError(f"Expected a profile mapping, got {type(raw).__name__}.")

    _scan_for_raw_data_keys(raw)

    payload: dict[str, Any] = {}
    for key in sorted(_PROFILE_ALLOWLIST - {"columns"}):
        if key in raw:
            payload[key] = _scalar(raw[key], key)

    columns: list[dict[str, Any]] = []
    for index, column in enumerate(raw.get("columns") or []):
        if not isinstance(column, dict):
            raise PrivacyViolationError(
                f"Expected a column mapping at columns[{index}], got {type(column).__name__}."
            )
        clean: dict[str, Any] = {}
        for key in sorted(_COLUMN_ALLOWLIST):
            if key not in column:
                continue
            path = f"columns[{index}].{key}"
            value = column[key]
            if key == "numeric":
                if value is None:
                    continue  # non-measure column: the contract omits the key entirely
                clean[key] = _clean_numeric(value, path)
            elif key == "issues_detected":
                if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                    raise PrivacyViolationError(f"issues_detected at {path} must be a list of codes.")
                clean[key] = list(value)
            else:
                clean[key] = _scalar(value, path)
        columns.append(clean)
    payload["columns"] = columns

    # The privacy claim must be auditable after the fact, not merely asserted.
    logger.debug(f"Outbound Claude payload: {json.dumps(payload, sort_keys=True)}")
    return payload


def profile_fingerprint(profile: DataProfile | dict) -> str:
    """Stable hash of the *sanitized* payload. Used to stop Streamlit reruns
    from re-calling Claude for a profile that already has a plan (ISS-007)."""
    return hashlib.sha256(json.dumps(sanitize_profile(profile), sort_keys=True).encode()).hexdigest()


# --------------------------------------------------------------------------- #
# Planner
# --------------------------------------------------------------------------- #


def _extract_tool_input(response: Any) -> dict | None:
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) == "tool_use":
            return dict(block.input)
    return None


def _build_client() -> Any:
    """Import the SDK lazily: keeps Streamlit cold start fast and lets the
    test suite run against a stub with no network.

    Streamlit does not read ``.env``, so load it here - otherwise the documented
    setup (copy ``.env.example`` to ``.env``) silently does nothing. ``utf-8-sig``
    tolerates the BOM Notepad adds, which would otherwise hide the key behind an
    invisible first character. A variable already set in the shell wins.
    """
    from dotenv import load_dotenv

    # Explicit path: python-dotenv's default search walks the *caller's* stack
    # frame, which differs under `streamlit run`, pytest and `python -`.
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", encoding="utf-8-sig")

    import anthropic

    return anthropic.Anthropic()


def _translate_sdk_error(exc: Exception) -> PlannerError:
    """Map an SDK failure to one plain-language PlannerError, most specific first.

    The UI catches PlannerError and shows the message; anything else would
    surface as a raw traceback. Retryable and non-retryable causes get
    different wording so the user knows whether trying again can help.
    """
    if isinstance(exc, TypeError) and "authentication method" in str(exc):
        return PlannerError(
            "No Anthropic credentials found. Copy .env.example to .env and set "
            "ANTHROPIC_API_KEY, or export it in the shell that runs the app."
        )
    import anthropic

    if isinstance(exc, anthropic.AuthenticationError):
        return PlannerError(
            "Anthropic rejected the API key (401). Check ANTHROPIC_API_KEY in .env - "
            "it may be revoked, truncated, or pasted with a stray character."
        )
    if isinstance(exc, anthropic.PermissionDeniedError):
        return PlannerError(f"This API key is not permitted to use {MODEL} (403). Check its workspace and model access.")
    if isinstance(exc, anthropic.NotFoundError):
        return PlannerError(f"Model {MODEL!r} was not found (404). Check mcp_planner.MODEL.")
    if isinstance(exc, anthropic.RateLimitError):
        return PlannerError("Anthropic is rate-limiting this key (429). Wait a moment and try again.")
    if isinstance(exc, anthropic.APIStatusError):
        return PlannerError(f"Anthropic returned HTTP {exc.status_code}: {exc.message}")
    if isinstance(exc, anthropic.APIConnectionError):
        return PlannerError("Could not reach the Anthropic API. Check the network connection and any proxy settings.")
    return PlannerError(f"Unexpected error calling Claude: {type(exc).__name__}: {exc}")


def generate_execution_plan(
    profile: DataProfile | dict,
    *,
    client: Any | None = None,
    max_attempts: int = 2,
) -> ExecutionPlan:
    """Turn a data profile into a validated execution plan.

    Args:
        profile: Interface 1 payload from ``src.backend.profiler.profile_dataframe``.
        client: Injected Anthropic client. Defaults to a real one; tests pass a
            stub so the suite makes no API calls.
        max_attempts: Retries when the model answers without calling the tool.

    Raises:
        PrivacyViolationError: if the profile would leak row data (before I/O).
        PlannerError: on refusal, on no tool call, or on an invalid plan.
    """
    payload = sanitize_profile(profile)
    client = client or _build_client()

    tool_input = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.beta.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                thinking={"type": "adaptive"},
                tools=[PROPOSE_EXECUTION_PLAN_TOOL],
                tool_choice={"type": "auto"},
                betas=[REFUSAL_FALLBACK_BETA],
                fallbacks="default",
                messages=[{"role": "user", "content": json.dumps(payload, sort_keys=True)}],
            )
        except (PlannerError, AssertionError):
            raise
        except Exception as exc:  # noqa: BLE001 - every SDK failure becomes one user-facing type
            raise _translate_sdk_error(exc) from exc

        # Opus 5 can decline with HTTP 200 and no tool_use block. Check this
        # before touching response.content, or the miss surfaces as StopIteration.
        if getattr(response, "stop_reason", None) == "refusal":
            details = getattr(response, "stop_details", None)
            explanation = getattr(details, "explanation", None) or "no explanation given"
            raise PlannerError(f"Claude declined to plan for this dataset: {explanation}")

        tool_input = _extract_tool_input(response)
        if tool_input is not None:
            break
        logger.warning(f"No tool_use block on attempt {attempt} of {max_attempts}")

    if tool_input is None:
        raise PlannerError(f"Claude did not call propose_execution_plan after {max_attempts} attempts.")

    try:
        output = PlannerOutput.model_validate(tool_input)
    except ValidationError as exc:
        # Total rejection. A partially-valid plan means the model misunderstood
        # the schema, which is exactly when its other actions deserve least trust.
        raise PlannerError(f"Claude returned a plan that failed validation: {exc}") from exc

    # Ids are minted here, never requested from the model: models are poor
    # entropy sources and a duplicated id corrupts the audit trail. The tier is
    # derived by the backend policy - a permission is not accepted on faith.
    actions: list[ProposedAction] = []
    for planned in output.actions:
        contract = {**planned.model_dump(), "action_id": uuid.uuid4().hex,
                    "autonomy_tier": "review_required", "requires_review": True}
        actions.append(ProposedAction.model_validate(annotate(contract)))

    return ExecutionPlan(
        plan_id=uuid.uuid4().hex,
        dataset_summary=output.dataset_summary,
        actions=actions,
        analysis_questions=output.analysis_questions,
    )
