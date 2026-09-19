import json
from copy import deepcopy
from typing import Any

from skill_lab.agent import AgentConfig, run_agent
from skill_lab.mock_model import ModelResponse
from skill_lab.models import Outcome, Task
from skill_lab.tools import ToolEnvironment


class _ScriptedModel:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def complete(self, request: dict[str, Any]) -> ModelResponse:
        self.requests.append(deepcopy(request))
        return self.responses.pop(0)


def _task() -> Task:
    return Task(
        id="IR-TR-01",
        input="Handle IR-TR-01: customer reports an outage.",
        available_tools=["get_ticket", "get_customer"],
        expected_outcome={
            "ticket_id": "T01",
            "classification": "outage",
            "severity": "SEV1",
            "evidence": ["ticket", "customer"],
            "escalation_path": "pager",
            "final_status": "resolved",
            "required_updates": {"priority": "SEV1", "owner": "pager"},
        },
        invariants=["ticket_loaded", "customer_loaded"],
        split="train",
        max_calls=2,
    )


def _tools() -> ToolEnvironment:
    return ToolEnvironment(
        {
            "tickets": [
                {
                    "id": "T01",
                    "customer_id": "C01",
                    "service_id": "S-CORE",
                    "summary": "outage incident T01",
                    "impact": "regional",
                    "priority": "SEV1",
                    "status": "open",
                    "classification": "outage",
                }
            ],
            "customers": [
                {
                    "id": "C01",
                    "tier": "enterprise",
                    "region": "us",
                    "approval_required": True,
                }
            ],
            "services": [],
            "runbooks": [],
        }
    )


def _response(payload: dict[str, Any], tokens: int, latency: int) -> ModelResponse:
    content = json.dumps(payload, sort_keys=True)
    return ModelResponse(
        content=content,
        input_tokens=tokens // 2,
        output_tokens=tokens - tokens // 2,
        total_tokens=tokens,
        latency_ms=latency,
    )


def test_agent_captures_complete_trajectory() -> None:
    model = _ScriptedModel(
        [
            _response(
                {
                    "action": "tool",
                    "arguments": {"ticket_id": "T01"},
                    "tool": "get_ticket",
                },
                tokens=10,
                latency=4,
            ),
            _response(
                {
                    "action": "final",
                    "output": {"ticket_id": "T01", "status": "ready"},
                },
                tokens=6,
                latency=3,
            ),
        ]
    )

    trajectory = run_agent(
        task=_task(),
        skill_markdown="# Procedure\nLoad the ticket.",
        model=model,
        tools=_tools(),
        config=AgentConfig(max_steps=4, token_budget=100),
        seed=1729,
        experiment_id="exp-20000101T000000Z-00000000",
        skill_version="v001",
    )

    assert trajectory.outcome == Outcome.SUCCESS
    assert trajectory.final_output == {"ticket_id": "T01", "status": "ready"}
    assert [call.tool for call in trajectory.tool_calls] == ["get_ticket"]
    assert trajectory.tool_calls[0].result["ticket"]["id"] == "T01"
    assert trajectory.tokens == 16
    assert trajectory.latency_ms == 7
    assert [message["role"] for message in trajectory.messages] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert model.requests[0]["kind"] == "agent"
    assert model.requests[0]["tool_history"] == []
    assert len(model.requests[1]["tool_history"]) == 1


def test_agent_records_budget_exhaustion() -> None:
    model = _ScriptedModel(
        [
            _response(
                {
                    "action": "tool",
                    "arguments": {"ticket_id": "T01"},
                    "tool": "get_ticket",
                },
                tokens=4,
                latency=2,
            ),
            _response(
                {
                    "action": "tool",
                    "arguments": {"customer_id": "C01"},
                    "tool": "get_customer",
                },
                tokens=4,
                latency=2,
            ),
            _response({"action": "final", "output": {"ticket_id": "T01"}}, 4, 2),
        ]
    )

    trajectory = run_agent(
        task=_task(),
        skill_markdown=None,
        model=model,
        tools=_tools(),
        config=AgentConfig(max_steps=5, token_budget=3),
        seed=1729,
        experiment_id="exp-20000101T000000Z-00000000",
        skill_version=None,
    )

    assert trajectory.outcome == Outcome.BUDGET_EXHAUSTED
    assert trajectory.tokens == 8
    assert len(trajectory.tool_calls) == 1
    assert trajectory.tool_calls[0].tool == "get_ticket"
    assert len(model.requests) == 2
    assert trajectory.final_output == {}


def test_agent_records_invalid_tool_call() -> None:
    model = _ScriptedModel(
        [
            _response(
                {
                    "action": "tool",
                    "arguments": {},
                    "tool": "missing_tool",
                },
                tokens=5,
                latency=1,
            ),
            _response({"action": "final", "output": {"unexpected": True}}, 5, 1),
        ]
    )

    trajectory = run_agent(
        task=_task(),
        skill_markdown=None,
        model=model,
        tools=_tools(),
        config=AgentConfig(max_steps=4, token_budget=100),
        seed=1729,
        experiment_id="exp-20000101T000000Z-00000000",
        skill_version="v001",
    )

    assert trajectory.outcome == Outcome.TOOL_ERROR
    assert len(trajectory.tool_calls) == 1
    assert trajectory.tool_calls[0].tool == "missing_tool"
    assert trajectory.tool_calls[0].result["error"]["code"] == "unknown_tool"
    assert len(model.requests) == 1
    assert trajectory.final_output == {}
