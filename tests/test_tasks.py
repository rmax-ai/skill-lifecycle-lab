from pathlib import Path

from skill_lab.models import Split
from skill_lab.tasks import load_tasks, tasks_for_split

_DATASET = Path(__file__).resolve().parents[1] / "datasets" / "incident_tasks.json"
_EXPECTED_OUTCOME_KEYS = {
    "ticket_id",
    "classification",
    "severity",
    "evidence",
    "escalation_path",
    "final_status",
    "required_updates",
}
_INVARIANT_VOCABULARY = {
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


def test_loads_24_sorted_tasks() -> None:
    tasks = load_tasks(_DATASET)

    assert len(tasks) == 24
    assert [task.id for task in tasks] == sorted(task.id for task in tasks)
    assert {
        split: sum(task.split.value == split for task in tasks)
        for split in ("train", "validation", "test")
    } == {"train": 12, "validation": 6, "test": 6}


def test_split_selection() -> None:
    tasks = load_tasks(_DATASET)
    original = list(tasks)
    train_tasks = tasks_for_split(tasks, Split.TRAIN)

    assert [task.id for task in train_tasks] == [f"IR-TR-{index:02d}" for index in range(1, 13)]
    assert len(train_tasks) == 12
    assert train_tasks[0] is next(task for task in tasks if task.id == "IR-TR-01")
    assert tasks == original


def test_task_outcome_shape() -> None:
    for task in load_tasks(_DATASET):
        assert set(task.expected_outcome) == _EXPECTED_OUTCOME_KEYS
        assert set(task.invariants) <= _INVARIANT_VOCABULARY
