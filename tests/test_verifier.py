import json
from pathlib import Path

from skill_lab.models import Task, Trajectory
from skill_lab.tasks import load_tasks
from skill_lab.tools import ToolEnvironment
from skill_lab.verifier import verify

_ROOT = Path(__file__).resolve().parents[1]
_DATASET = _ROOT / "datasets" / "incident_tasks.json"
_FIXTURES = _ROOT / "datasets" / "tool_world.json"


def _task() -> Task:
    return next(task for task in load_tasks(_DATASET) if task.id == "IR-TR-01")


def _actions() -> list[tuple[str, dict[str, str]]]:
    return [
        ("get_ticket", {"ticket_id": "T01"}),
        ("get_customer", {"customer_id": "C01"}),
        ("get_service_status", {"service_id": "S-CORE"}),
        ("get_runbook", {"service_id": "S-CORE"}),
        ("request_approval", {"ticket_id": "T01", "reason": "incident review"}),
        ("escalate_ticket", {"ticket_id": "T01", "path": "pager"}),
        (
            "update_ticket",
            {
                "ticket_id": "T01",
                "priority": "SEV1",
                "owner": "pager",
                "status": "resolved",
            },
        ),
    ]


def _trajectory(
    actions: list[tuple[str, dict[str, str]]] | None = None,
    *,
    final_output: dict | str | None = None,
) -> Trajectory:
    fixtures = json.loads(_FIXTURES.read_text(encoding="utf-8"))
    environment = ToolEnvironment(fixtures)
    for tool, arguments in actions or _actions():
        environment.execute(tool, arguments)
    task = _task()
    return Trajectory(
        experiment_id="exp-20000101T000000Z-00000000",
        task_id=task.id,
        skill_version="v001",
        messages=[{"role": "assistant", "content": "complete"}],
        tool_calls=environment.calls,
        final_output=task.expected_outcome if final_output is None else final_output,
        tokens=10,
        latency_ms=5,
        outcome="success",
    )


def test_valid_trajectory_succeeds() -> None:
    result = verify(_task(), _trajectory())

    assert result.success is True
    assert all(result.checks.values())
    assert result.checks == {
        "schema_valid": True,
        "correct_ticket": True,
        "correct_classification": True,
        "correct_severity": True,
        "correct_escalation_path": True,
        "correct_final_status": True,
        "correct_updates": True,
        "required_evidence": True,
        "required_actions": True,
        "ordering": True,
        "no_forbidden_actions": True,
        "no_tool_errors": True,
        "no_unnecessary_actions": True,
        "invariants_satisfied": True,
    }
    assert result.evidence["required_evidence"] == {
        "ticket": [1],
        "customer": [2],
        "status": [3],
        "runbook": [4],
    }


def test_missing_evidence_fails() -> None:
    actions = _actions()
    actions.pop(2)

    result = verify(_task(), _trajectory(actions))

    assert result.success is False
    assert result.checks["required_evidence"] is False
    assert result.checks["required_actions"] is False
    assert result.checks["invariants_satisfied"] is False
    assert result.evidence["required_evidence"]["status"] == []


def test_unapproved_escalation_fails() -> None:
    actions = _actions()
    actions.pop(4)

    result = verify(_task(), _trajectory(actions))

    assert result.success is False
    assert result.checks["ordering"] is False
    assert result.checks["no_forbidden_actions"] is True
    assert result.checks["correct_escalation_path"] is True

    actions[4] = ("escalate_ticket", {"ticket_id": "T01", "path": "forbidden-path"})
    forbidden_result = verify(_task(), _trajectory(actions))

    assert forbidden_result.success is False
    assert forbidden_result.checks["no_forbidden_actions"] is False
    assert forbidden_result.evidence["forbidden_actions"] == [5]


def test_update_before_escalation_fails() -> None:
    actions = _actions()
    actions[5], actions[6] = actions[6], actions[5]

    result = verify(_task(), _trajectory(actions))

    assert result.success is False
    assert result.checks["ordering"] is False
    assert result.checks["no_forbidden_actions"] is True
    assert result.checks["correct_updates"] is True


def test_tool_error_fails() -> None:
    actions = _actions()
    actions[1] = ("get_customer", {"customer_id": "C99"})

    result = verify(_task(), _trajectory(actions))

    assert result.success is False
    assert result.checks["no_tool_errors"] is False
    assert result.evidence["tool_errors"] == [2]
    assert result.checks["required_evidence"] is False


def test_unnecessary_call_fails() -> None:
    actions = [("search_tickets", {"query": "T01"}), *_actions()]

    result = verify(_task(), _trajectory(actions))

    assert result.success is False
    assert result.checks["no_unnecessary_actions"] is False
    assert result.checks["ordering"] is True
    assert result.checks["no_tool_errors"] is True
