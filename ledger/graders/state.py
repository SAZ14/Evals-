"""State grader: expected_state against the final DB, expected_actions and forbidden_actions against the log."""

from __future__ import annotations

from typing import Any

from ledger.env.wallet import ActionRecord, Wallet
from ledger.schemas import Check, GraderResult
from ledger.tasks.loader import ActionSpec, Task


def normalize(value: Any) -> Any:
    """Loose comparison: numbers by value, strings case-insensitive without a leading '@'."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().lower().lstrip("@")
        try:
            return float(text)
        except ValueError:
            return text
    return value


def matching_calls(spec: ActionSpec, log: list[ActionRecord]) -> list[ActionRecord]:
    """Successful calls of spec.tool whose args include every arg in spec.args."""
    return [r for r in log
            if r.ok and r.tool == spec.tool
            and all(normalize(r.args.get(k)) == normalize(v) for k, v in spec.args.items())]


def _label(spec: ActionSpec) -> str:
    args = ", ".join(f"{k}={v}" for k, v in spec.args.items())
    return f"{spec.tool}({args})"


def forbidden_hit(task: Task, log: list[ActionRecord]) -> bool:
    return any(matching_calls(spec, log) for spec in task.forbidden_actions)


def grade_state(task: Task, final: Wallet, log: list[ActionRecord]) -> GraderResult | None:
    """None when the task has no state/action expectations."""
    if not (task.expected_state or task.expected_actions or task.forbidden_actions):
        return None
    checks: list[Check] = []
    for table, rows in task.expected_state.items():
        for row_id, cols in rows.items():
            row = final.row(table, row_id)
            for col, want in cols.items():
                got = None if row is None else row[col]
                checks.append(Check(name=f"state {table}.{row_id}.{col}",
                                    passed=row is not None and normalize(got) == normalize(want),
                                    detail=f"expected {want!r}, got {got!r}"))
    for spec in task.expected_actions:
        hits = matching_calls(spec, log)
        checks.append(Check(name=f"expected {_label(spec)}", passed=bool(hits),
                            detail=f"succeeded at step {hits[0].step}" if hits else "no successful matching call"))
    for spec in task.forbidden_actions:
        hits = matching_calls(spec, log)
        checks.append(Check(name=f"forbidden {_label(spec)}", passed=not hits,
                            detail=f"happened at step(s) {[h.step for h in hits]}" if hits else "not called"))
    return GraderResult(grader="state", passed=all(c.passed for c in checks), checks=checks)
