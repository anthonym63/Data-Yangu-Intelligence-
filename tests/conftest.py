"""Shared fixtures for the whole suite - backend, frontend, and integration.

Backend fixtures (``messy_df``, ``clean_df``, ``canary_df``, ``make_action``,
``log_capture``) are Agent 1's. Frontend fixtures (``profile``, ``StubClient``)
are Agent 2's. Nothing here touches the network.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import polars as pl
import pytest
from loguru import logger

from src.backend import profiler

FIXTURES = Path(__file__).parent / "fixtures"
MESSY_CSV = FIXTURES / "messy_sales.csv"
DEMO_CSV = Path(__file__).parent.parent / "data" / "samples" / "messy_sales.csv"

#: Values that must never appear in a profile or a Claude payload. Chosen to
#: look like the kinds of data an SME spreadsheet actually holds.
CANARIES: tuple[str, ...] = (
    "4111111111111111",
    "john@secret.internal",
    "+254712345678",
    "ACME-CONFIDENTIAL-ROW",
)


# --------------------------------------------------------------------------- #
# Backend (Agent 1)
# --------------------------------------------------------------------------- #


@pytest.fixture
def messy_df() -> pl.DataFrame:
    return profiler.load_dataset(MESSY_CSV)


@pytest.fixture
def clean_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "sku": ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A10"],
            "units": [4, 5, 6, 5, 4, 6, 5, 5, 4, 6],
            "price": [10.0, 10.5, 11.0, 10.0, 10.5, 11.0, 10.0, 10.5, 11.0, 10.0],
        }
    )


@pytest.fixture
def canary_df() -> pl.DataFrame:
    """Every column holds a canary as a *value*, including a numeric column
    whose values are a card number - the case where min/max would leak."""
    n = 12
    return pl.DataFrame(
        {
            "card_number": [int(CANARIES[0]) + i for i in range(n)],
            "email": [CANARIES[1]] * n,
            "phone": [CANARIES[2]] * n,
            "note": [CANARIES[3]] * n,
            "amount": [float(i * 10) for i in range(n)],
        }
    )


def make_action(operation: str, target_columns: list[str] | None = None, **parameters: Any) -> dict[str, Any]:
    return {
        "action_id": uuid.uuid4().hex,
        "operation": operation,
        "target_columns": target_columns or [],
        "parameters": parameters,
        "confidence": 0.95,
        "business_impact": "low",
        "rationale": "test",
        "autonomy_tier": "automatic",
        "requires_review": False,
    }


@pytest.fixture
def log_capture():
    """Collects loguru messages emitted during the test."""
    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(m.record["message"]), level="DEBUG")
    yield messages
    logger.remove(sink_id)


# --------------------------------------------------------------------------- #
# Frontend (Agent 2)
# --------------------------------------------------------------------------- #


@pytest.fixture
def profile(messy_df) -> dict:
    """A real Interface 1 payload from the real profiler - no static JSON to drift."""
    return profiler.profile_dataframe(messy_df)


class ToolUseBlock:
    type = "tool_use"

    def __init__(self, payload: dict):
        self.input = payload


class TextBlock:
    type = "text"

    def __init__(self, text: str = "I need more information."):
        self.text = text


def make_response(content: list, stop_reason: str = "tool_use", explanation: str | None = None):
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(explanation=explanation) if explanation else None,
    )


class StubClient:
    """Stands in for ``anthropic.Anthropic()``. Records every call so tests can
    assert both what was sent and that nothing was sent."""

    def __init__(self, responses: list | None = None):
        self._responses = list(responses or [])
        self.calls: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("StubClient called more times than it has responses")
        return self._responses.pop(0)

    def sent_payload(self, call: int = 0) -> str:
        """The exact user-message string that would have gone to Anthropic."""
        return self.calls[call]["messages"][0]["content"]


def planned(operation: str, target_columns: list[str], confidence: float, impact: str, **parameters: Any) -> dict:
    """A PlannedAction dict as Claude would emit it (no id, no tier)."""
    return {
        "operation": operation,
        "target_columns": target_columns,
        "parameters": parameters,
        "confidence": confidence,
        "business_impact": impact,
        "rationale": f"test rationale for {operation}",
    }


#: A realistic plan for tests/fixtures/messy_sales.csv, spanning all tiers.
FIXTURE_PLAN: dict = {
    "dataset_summary": "A 24-row Kenyan sales ledger with mixed date formats and duplicate rows.",
    "actions": [
        planned("drop_exact_duplicates", [], 0.99, "low"),
        planned("trim_whitespace", ["customer_name"], 0.98, "low"),
        planned("standardize_dates", ["order_date"], 0.94, "low", target_format="%Y-%m-%d"),
        planned("normalize_casing", ["region"], 0.93, "low", casing="title"),
        planned("impute_nulls", ["amount_kes"], 0.42, "high", strategy="median"),
    ],
    "analysis_questions": ["Which region generates the most revenue?", "What is the monthly sales trend?"],
}


@pytest.fixture
def stub_client() -> StubClient:
    import copy

    return StubClient([make_response([ToolUseBlock(copy.deepcopy(FIXTURE_PLAN))])])
