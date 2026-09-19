"""Load and select tasks from the validated operator corpus."""

from __future__ import annotations

import json
import os
from operator import attrgetter
from pathlib import Path

from skill_lab.models import Task

_EXPECTED_OUTCOME_KEYS = frozenset(
    {
        "ticket_id",
        "classification",
        "severity",
        "evidence",
        "escalation_path",
        "final_status",
        "required_updates",
    }
)
_INVARIANT_VOCABULARY = frozenset(
    {
        "ticket_loaded",
        "customer_loaded",
        "runbook_loaded",
        "evidence_complete",
        "correct_escalation",
        "update_after_escalation",
        "no_forbidden_escalation",
        "no_unnecessary_calls",
        "service_checked",
        "approval_before_escalation",
    }
)


def _expanded(path: Path) -> Path:
    return Path(os.path.expanduser(str(path)))


def _assert_task_shape(task: Task) -> None:
    assert set(task.expected_outcome) == _EXPECTED_OUTCOME_KEYS
    assert set(task.invariants) <= _INVARIANT_VOCABULARY


def load_tasks(path: Path) -> list[Task]:
    """Parse the validated JSON task array and return tasks sorted by ID."""

    with _expanded(path).open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError("task dataset must be a JSON array")

    tasks = [Task.model_validate(record) for record in payload]
    for task in tasks:
        _assert_task_shape(task)
    return sorted(tasks, key=attrgetter("id"))


def tasks_for_split(tasks: list[Task], split: str) -> list[Task]:
    """Return a sorted shallow selection without changing the source list."""

    return sorted(
        (task for task in tasks if task.split == split),
        key=attrgetter("id"),
    )
