"""Audit log: every step recorded, every step reversible, exports honest."""

from __future__ import annotations

import json

import pytest
from polars.testing import assert_frame_equal

from src.backend.audit_logger import AuditLog, RollbackError
from tests.conftest import make_action


def _chain(df):
    log = AuditLog(df, source_name="messy_sales.csv")
    a1 = make_action("drop_exact_duplicates")
    a2 = make_action("trim_whitespace", ["customer_name"])
    a3 = make_action("normalize_casing", ["region"], casing="title")
    log.apply(a1)
    log.apply(a2)
    log.apply(a3)
    return log, (a1, a2, a3)


def test_apply_chain_records_every_step(messy_df):
    log, (a1, a2, a3) = _chain(messy_df)
    assert len(log) == 3
    assert [e["action_id"] for e in log.entries] == [a1["action_id"], a2["action_id"], a3["action_id"]]
    assert log.entries[0]["rows_before"] == 24 and log.entries[0]["rows_after"] == 21
    assert log.entries[1]["rows_before"] == 21  # each step starts from the previous result
    assert log.current.height == 21
    assert_frame_equal(log.original, messy_df)  # original never touched


def test_rollback_to_first_step_restores_original(messy_df):
    """The load-bearing test: a full undo is byte-for-byte the original."""
    log, (a1, _, _) = _chain(messy_df)
    restored = log.rollback(a1["action_id"])
    assert_frame_equal(restored, messy_df)
    assert_frame_equal(log.current, messy_df)
    assert len(log) == 0


def test_rollback_all(messy_df):
    log, _ = _chain(messy_df)
    assert_frame_equal(log.rollback_all(), messy_df)
    assert_frame_equal(log.rollback_all(), messy_df)  # no-op on empty log


def test_rollback_mid_chain_keeps_earlier_steps(messy_df):
    log, (a1, a2, a3) = _chain(messy_df)
    restored = log.rollback(a2["action_id"])
    assert len(log) == 1 and log.entries[0]["action_id"] == a1["action_id"]
    assert restored.height == 21  # a1 (dedupe) still applied
    assert any(n != n.strip() for n in restored.get_column("customer_name").to_list())  # a2 undone


def test_rollback_frees_snapshots_and_allows_reapply(messy_df):
    log, (a1, a2, a3) = _chain(messy_df)
    log.rollback(a2["action_id"])
    log.apply(a2)  # same id is reusable once rolled back
    assert len(log) == 2


def test_rollback_unknown_id(messy_df):
    log, _ = _chain(messy_df)
    with pytest.raises(RollbackError):
        log.rollback("nope")


def test_rollback_is_logged(messy_df, log_capture):
    log, (a1, _, _) = _chain(messy_df)
    log.rollback(a1["action_id"])
    assert any("rollback" in m and "undid 3 step(s)" in m for m in log_capture)


def test_duplicate_action_id_rejected(messy_df):
    log = AuditLog(messy_df)
    a = make_action("drop_exact_duplicates")
    log.apply(a)
    with pytest.raises(ValueError, match="already"):
        log.apply(a)


def test_record_external_step(messy_df):
    from src.backend import cleaner

    log = AuditLog(messy_df)
    a = make_action("trim_whitespace", ["region"])
    after, entry = cleaner.apply(messy_df, a)
    log.record(entry, messy_df, after)
    assert len(log) == 1 and log.current is after
    assert_frame_equal(log.rollback(a["action_id"]), messy_df)


def test_every_step_logs_before_after(messy_df, log_capture):
    _chain(messy_df)
    transitions = [m for m in log_capture if m.startswith("audit | ") and " -> " in m]
    assert len(transitions) == 3


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #


def test_export_json(messy_df):
    log, (a1, a2, a3) = _chain(messy_df)
    data = json.loads(log.export("json"))
    assert data["source"] == "messy_sales.csv"
    assert data["original_rows"] == 24 and data["current_rows"] == 21
    assert [s["action_id"] for s in data["steps"]] == [a1["action_id"], a2["action_id"], a3["action_id"]]
    assert all({"timestamp", "summary", "human_decision", "reversible"} <= set(s) for s in data["steps"])


def test_export_html_escapes_content(messy_df):
    log = AuditLog(messy_df, source_name="<script>alert(1)</script>.csv")
    log.apply(make_action("trim_whitespace", ["customer_name"]))
    out = log.export("html")
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert "<table>" in out and "trim_whitespace" in out


def test_export_html_empty_log(messy_df):
    assert "No transformations applied" in AuditLog(messy_df).export("html")


def test_export_rejects_unknown_format(messy_df):
    with pytest.raises(ValueError):
        AuditLog(messy_df).export("pdf")
