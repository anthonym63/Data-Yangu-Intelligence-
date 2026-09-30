"""Reversible audit log (CLAUDE.md 3.3: every transformation logs a
before/after state transition).

Two layers:

- Module functions ``make_entry`` / ``log_transition`` - stateless; used by
  ``cleaner.apply`` so a single step can be audited without a session.
- ``AuditLog`` - stateful; owns the current frame, a before-snapshot per step,
  and rollback. This is what a session holds.

Snapshots are whole Polars frames. Polars columns are immutable Arrow buffers
shared between frames, so a snapshot after a one-column change costs roughly
one column, not one frame. Fine for the spreadsheet sizes in scope.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import polars as pl
from loguru import logger

from src.contracts import AuditEntry, ProposedAction

if TYPE_CHECKING:  # avoid an import cycle at runtime; cleaner imports us
    from src.backend.cleaner import Delta


class RollbackError(KeyError):
    """``action_id`` is not in the log."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Stateless
# --------------------------------------------------------------------------- #


def make_entry(action: ProposedAction, delta: "Delta", *, human_decision: str = "approved") -> AuditEntry:
    from src.backend.cleaner import summarize  # local import: cleaner imports this module

    return {
        "action_id": str(action["action_id"]),
        "timestamp": _now(),
        "operation": str(action["operation"]),
        "target_columns": [str(c) for c in action.get("target_columns") or []],
        "rows_before": int(delta.rows_before),
        "rows_after": int(delta.rows_after),
        "cells_changed": int(delta.cells_changed),
        "human_decision": human_decision,
        "reversible": True,
        "summary": summarize(action, delta),
    }


def log_transition(entry: AuditEntry) -> None:
    """The before/after line. One per applied step, at INFO."""
    logger.info(
        "audit | {op} | rows {before} -> {after} | cells_changed={cells} | decision={decision} | action_id={aid}",
        op=entry["operation"],
        before=entry["rows_before"],
        after=entry["rows_after"],
        cells=entry["cells_changed"],
        decision=entry["human_decision"],
        aid=entry["action_id"],
    )


# --------------------------------------------------------------------------- #
# Stateful
# --------------------------------------------------------------------------- #


class AuditLog:
    """Holds the original frame, the current frame, and one before-snapshot per
    applied step so any step can be undone."""

    def __init__(self, original: pl.DataFrame, *, source_name: str = "dataset") -> None:
        self._original = original
        self._current = original
        self._source_name = source_name
        self._entries: list[AuditEntry] = []
        self._before: dict[str, pl.DataFrame] = {}

    # -- state ------------------------------------------------------------- #

    @property
    def original(self) -> pl.DataFrame:
        return self._original

    @property
    def current(self) -> pl.DataFrame:
        return self._current

    @property
    def entries(self) -> list[AuditEntry]:
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    # -- apply ------------------------------------------------------------- #

    def apply(self, action: ProposedAction, *, human_decision: str = "approved") -> AuditEntry:
        """Run ``action`` against the current frame and record it."""
        from src.backend.cleaner import apply as _apply  # local import: cycle

        aid = str(action["action_id"])
        if aid in self._before:
            raise ValueError(f"action_id {aid!r} has already been applied; mint a new id to re-run")
        before = self._current
        after, entry = _apply(before, action, human_decision=human_decision)
        self._before[aid] = before
        self._current = after
        self._entries.append(entry)
        return entry

    def record(self, entry: AuditEntry, before: pl.DataFrame, after: pl.DataFrame) -> None:
        """Register a step that was applied elsewhere (e.g. via ``cleaner.apply``)."""
        aid = entry["action_id"]
        if aid in self._before:
            raise ValueError(f"action_id {aid!r} already recorded")
        self._before[aid] = before
        self._current = after
        self._entries.append(entry)

    # -- rollback ---------------------------------------------------------- #

    def rollback(self, action_id: str) -> pl.DataFrame:
        """Undo ``action_id`` and everything after it. Returns the restored frame.

        Rolled-back entries leave the active log and are noted at INFO, so the
        trail records that an undo happened without pretending the steps
        still stand.
        """
        idx = next((i for i, e in enumerate(self._entries) if e["action_id"] == action_id), None)
        if idx is None:
            raise RollbackError(action_id)
        undone = self._entries[idx:]
        self._current = self._before[action_id]
        for e in undone:
            self._before.pop(e["action_id"], None)
        del self._entries[idx:]
        logger.info(
            "audit | rollback to before {aid} | undid {n} step(s) | rows now {rows}",
            aid=action_id,
            n=len(undone),
            rows=self._current.height,
        )
        return self._current

    def rollback_all(self) -> pl.DataFrame:
        if self._entries:
            return self.rollback(self._entries[0]["action_id"])
        return self._current

    # -- export ------------------------------------------------------------ #

    def export(self, fmt: str = "json") -> str:
        if fmt == "json":
            return json.dumps(self._payload(), indent=2, default=str)
        if fmt == "html":
            return self._html()
        raise ValueError(f"export format must be 'json' or 'html', got {fmt!r}")

    def _payload(self) -> dict[str, Any]:
        return {
            "source": self._source_name,
            "exported_at": _now(),
            "original_rows": self._original.height,
            "current_rows": self._current.height,
            "steps": self._entries,
        }

    def _html(self) -> str:
        e = html.escape
        head = (
            "<!doctype html><meta charset='utf-8'>"
            f"<title>Change log - {e(self._source_name)}</title>"
            "<style>body{font-family:system-ui,sans-serif;margin:2rem}"
            "table{border-collapse:collapse}th,td{border:1px solid #ccc;padding:.4rem .6rem;text-align:left}"
            "th{background:#f3f3f3}</style>"
            f"<h1>Change log - {e(self._source_name)}</h1>"
            f"<p>{self._original.height:,} rows originally, {self._current.height:,} now. "
            f"Exported {e(_now())}.</p>"
        )
        if not self._entries:
            return head + "<p>No transformations applied.</p>"
        rows = "".join(
            "<tr>"
            f"<td>{i + 1}</td><td>{e(x['timestamp'])}</td><td>{e(x['operation'])}</td>"
            f"<td>{e(', '.join(x['target_columns']))}</td>"
            f"<td>{x['rows_before']:,}</td><td>{x['rows_after']:,}</td><td>{x['cells_changed']:,}</td>"
            f"<td>{e(x['human_decision'])}</td><td>{e(x['summary'])}</td>"
            "</tr>"
            for i, x in enumerate(self._entries)
        )
        return (
            head
            + "<table><thead><tr><th>#</th><th>When (UTC)</th><th>Operation</th><th>Columns</th>"
            "<th>Rows before</th><th>Rows after</th><th>Cells changed</th><th>Decision</th><th>Summary</th>"
            "</tr></thead><tbody>" + rows + "</tbody></table>"
        )


__all__ = ["AuditLog", "RollbackError", "log_transition", "make_entry"]
