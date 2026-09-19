import json

import pytest

from skill_lab.agent import AgentConfig, _response_values, run_agent
from skill_lab.experiment import _evaluate_slot
from skill_lab.mock_model import ModelResponse
from skill_lab.models import Condition, RunRecord, Task
from skill_lab.tools import ToolEnvironment


def _task() -> Task:
    return Task(
        id="IR-TR-01",
        input="Handle the placeholder incident.",
        available_tools=["get_ticket"],
        expected_outcome={
            "ticket_id": "T01",
            "classification": "outage",
            "severity": "SEV1",
            "evidence": ["ticket"],
            "escalation_path": "pager",
            "final_status": "resolved",
            "required_updates": {"priority": "SEV1", "owner": "pager"},
        },
        invariants=[],
        split="train",
        max_calls=2,
    )


def _final_output() -> dict[str, object]:
    return {
        "ticket_id": "T01",
        "classification": "outage",
        "severity": "SEV1",
        "evidence": ["ticket"],
        "escalation_path": "pager",
        "final_status": "resolved",
        "required_updates": {"priority": "SEV1", "owner": "pager"},
    }


def _fixtures() -> dict[str, object]:
    return {
        "tickets": [
            {
                "id": "T01",
                "customer_id": "C01",
                "service_id": "S-CORE",
                "summary": "placeholder incident",
                "impact": "regional",
                "priority": "SEV1",
                "status": "open",
                "classification": "outage",
            }
        ]
    }


def test_total_tokens_must_equal_components() -> None:
    response = {
        "content": json.dumps({"action": "final", "output": {}}),
        "input_tokens": 3,
        "output_tokens": 4,
        "latency_ms": 0,
    }

    with pytest.raises(ValueError, match="total_tokens"):
        _response_values({**response, "total_tokens": 8})

    assert _response_values({**response, "total_tokens": 7})[3] == 7
    assert _response_values(response)[3] == 7

    trajectory = run_agent(
        task=_task(),
        skill_markdown=None,
        model=_MismatchedTotalModel(),
        tools=ToolEnvironment(_fixtures()),
        config=AgentConfig(max_steps=1, token_budget=100),
        seed=1729,
        experiment_id="exp-20000101T000000Z-00000000",
        skill_version=None,
    )
    assert trajectory.outcome == "model_error"


class _MismatchedTotalModel:
    def complete(self, _request: dict[str, object]) -> ModelResponse:
        return ModelResponse(
            model="placeholder-model",
            content=json.dumps({"action": "final", "output": _final_output()}),
            input_tokens=2,
            output_tokens=3,
            total_tokens=8,
            latency_ms=4,
        )


class _InvalidTotalModel:
    def complete(self, _request: dict[str, object]) -> dict[str, object]:
        return {
            "model": "placeholder-model",
            "content": json.dumps({"action": "final", "output": _final_output()}),
            "input_tokens": 2,
            "output_tokens": 3,
            "total_tokens": "invalid",
            "latency_ms": 4,
        }


class _StructuralFailureModel:
    def complete(self, _request: dict[str, object]) -> dict[str, object]:
        return {
            "model": "placeholder-model",
            "input_tokens": 2,
            "output_tokens": 3,
            "total_tokens": 5,
            "latency_ms": 4,
        }


def test_rejected_total_mismatch_run_accounts_call() -> None:
    trajectory = run_agent(
        task=_task(),
        skill_markdown=None,
        model=_MismatchedTotalModel(),
        tools=ToolEnvironment(_fixtures()),
        config=AgentConfig(max_steps=1, token_budget=100),
        seed=1729,
        experiment_id="exp-20000101T000000Z-00000000",
        skill_version=None,
    )

    assert trajectory.outcome == "model_error"
    assert trajectory.tokens == 5
    assert trajectory.latency_ms == 4
    assert trajectory.messages[-1]["role"] == "assistant"

    run = _evaluate_slot(
        task=_task(),
        fixtures=_fixtures(),
        model=_MismatchedTotalModel(),
        agent_config=AgentConfig(max_steps=1, token_budget=100),
        experiment_id="exp-20000101T000000Z-00000000",
        condition=Condition.SEED,
        skill_markdown=None,
        skill_version="v001",
        split="train",
        run_slot=0,
        seed=1729,
        pricing=None,
    )

    assert run.outcome == "model_error"
    assert run.input_tokens == 2
    assert run.output_tokens == 3
    assert run.total_tokens == 5
    assert run.total_tokens == run.input_tokens + run.output_tokens
    assert run.latency_ms == 4


def test_error_path_records_stay_consistent() -> None:
    for model in (_InvalidTotalModel(), _StructuralFailureModel()):
        run = _evaluate_slot(
            task=_task(),
            fixtures=_fixtures(),
            model=model,
            agent_config=AgentConfig(max_steps=1, token_budget=100),
            experiment_id="exp-20000101T000000Z-00000000",
            condition=Condition.SEED,
            skill_markdown=None,
            skill_version="v001",
            split="train",
            run_slot=0,
            seed=1729,
            pricing=None,
        )

        assert run.outcome == "model_error"
        assert run.total_tokens == run.input_tokens + run.output_tokens
        assert run.total_tokens == run.trajectory.tokens


class _ResponseModel:
    model_id = "fallback-model"

    def __init__(self) -> None:
        self._responses = iter(
            (
                ModelResponse(
                    model="zeta-model",
                    content=json.dumps(
                        {
                            "action": "tool",
                            "tool": "get_ticket",
                            "arguments": {"ticket_id": "T01"},
                        }
                    ),
                    input_tokens=2,
                    output_tokens=3,
                    total_tokens=5,
                    latency_ms=0,
                ),
                ModelResponse(
                    model="alpha-model",
                    content=json.dumps({"action": "final", "output": _final_output()}),
                    input_tokens=4,
                    output_tokens=1,
                    total_tokens=5,
                    latency_ms=0,
                ),
            )
        )

    def complete(self, _request: dict[str, object]) -> ModelResponse:
        return next(self._responses)


def test_evaluate_slot_persists_response_model() -> None:
    run = _evaluate_slot(
        task=_task(),
        fixtures=_fixtures(),
        model=_ResponseModel(),
        agent_config=AgentConfig(max_steps=2, token_budget=100),
        experiment_id="exp-20000101T000000Z-00000000",
        condition=Condition.SEED,
        skill_markdown=None,
        skill_version="v001",
        split="train",
        run_slot=0,
        seed=1729,
        pricing=None,
    )

    assert run.model_id == "alpha-model|zeta-model"
    assert run.input_tokens == 6
    assert run.output_tokens == 4
    assert run.total_tokens == 10


def test_run_record_model_id_round_trip() -> None:
    trajectory = {
        "experiment_id": "exp-20000101T000000Z-00000000",
        "task_id": "IR-TR-01",
        "skill_version": "v001",
        "messages": [],
        "tool_calls": [],
        "final_output": {},
        "tokens": 0,
        "latency_ms": 0,
        "outcome": "failure",
    }
    verification = {"success": False, "checks": {}, "evidence": {}}
    run = RunRecord(
        run_id="run-0000000000000000",
        experiment_id="exp-20000101T000000Z-00000000",
        task_id="IR-TR-01",
        split="train",
        condition_name="seed",
        skill_version="v001",
        model_id="alpha-model|zeta-model",
        run_slot=0,
        seed=1729,
        outcome="failure",
        success=False,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        latency_ms=0,
        estimated_cost_usd=0.0,
        trajectory=trajectory,
        verification=verification,
    )

    assert RunRecord.model_validate_json(run.model_dump_json()) == run
