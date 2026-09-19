import json
import socket
from typing import Any

from skill_lab.mock_model import ModelResponse, ScriptedMockModel


def _task(task_id: str) -> dict[str, Any]:
    return {
        "id": task_id,
        "input": f"Handle {task_id}: determine procedure.",
        "expected_outcome": {
            "ticket_id": "T14" if task_id == "IR-VA-02" else "T15",
            "classification": "degradation" if task_id == "IR-VA-02" else "security",
            "severity": "SEV2" if task_id == "IR-VA-02" else "SEV1",
            "evidence": (
                ["ticket", "customer", "status", "runbook"]
                if task_id == "IR-VA-02"
                else ["ticket", "customer", "runbook"]
            ),
            "escalation_path": "service-desk" if task_id == "IR-VA-02" else "security",
            "final_status": "resolved",
            "required_updates": {
                "priority": "SEV2" if task_id == "IR-VA-02" else "SEV1",
                "owner": "service-desk" if task_id == "IR-VA-02" else "security",
            },
        },
        "invariants": [
            "ticket_loaded",
            "customer_loaded",
            "runbook_loaded",
            "evidence_complete",
            "correct_escalation",
            "update_after_escalation",
            "no_forbidden_escalation",
            "no_unnecessary_calls",
            "approval_before_escalation",
        ],
    }


def _agent_request(task_id: str, skill_version: str) -> dict[str, Any]:
    return {
        "kind": "agent",
        "task": _task(task_id),
        "task_id": task_id,
        "skill_version": skill_version,
        "tool_history": [],
    }


def _mutation_request(mode: str, parent_version: str) -> dict[str, Any]:
    return {
        "kind": "mutation",
        "mode": mode,
        "parent_version": parent_version,
        "current_skill": {
            "name": "incident-response",
            "version": parent_version,
            "description": "End-to-end procedure for handling customer incident tickets.",
            "markdown": "# Procedure\n",
        },
        "train_failures": [
            {
                "task_id": "IR-TR-01",
                "task_input": "Handle the training task.",
                "trajectory": {},
                "verification": {},
            }
        ],
    }


def test_mock_agent_response_is_deterministic() -> None:
    model = ScriptedMockModel(seed=1729)
    request = _agent_request("IR-VA-02", "v001")

    first = model.complete(request)
    second = model.complete(request)

    assert isinstance(first, ModelResponse)
    assert first.model_dump_json() == second.model_dump_json()
    assert first.content == json.dumps(json.loads(first.content), sort_keys=True)
    assert first.payload == {
        "action": "tool",
        "arguments": {"ticket_id": "T14"},
        "tool": "get_ticket",
    }


def test_mock_mutation_promote_candidate() -> None:
    response = ScriptedMockModel(seed=1729).complete(_mutation_request("promote", "v001"))
    candidate = json.loads(response.content)

    assert candidate["candidate_version"] == "v002"
    assert candidate["parent_version"] == "v001"
    assert candidate["generation"] == 1
    assert "approval" in candidate["procedural_change"].lower()
    assert "request approval" in candidate["candidate_markdown"].lower()
    assert response.content == json.dumps(candidate, sort_keys=True)


def test_mock_mutation_reject_candidate() -> None:
    response = ScriptedMockModel(seed=1729).complete(_mutation_request("reject", "v002"))
    candidate = json.loads(response.content)

    assert candidate["candidate_version"] == "v003"
    assert candidate["parent_version"] == "v002"
    assert candidate["generation"] == 2
    assert "restricted path" in candidate["procedural_change"]
    assert "forbidden" in candidate["rationale"]


def test_mock_never_uses_network(monkeypatch: Any) -> None:
    def fail_socket(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("the scripted model must not open a socket")

    monkeypatch.setattr(socket, "socket", fail_socket)
    response = ScriptedMockModel(seed=1729).complete(_agent_request("IR-TR-01", "v002"))

    assert response.model == "mock-incident-v1"
