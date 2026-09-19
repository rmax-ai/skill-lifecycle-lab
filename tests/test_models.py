import pytest
from pydantic import ValidationError

from skill_lab.models import (
    CandidateSkill,
    Condition,
    Decision,
    MutationStatus,
    Outcome,
    PromotionDecision,
    RejectionReason,
    RunRecord,
    Split,
    Task,
    ToolCall,
    Trajectory,
    VerificationResult,
)


def _task_payload() -> dict[str, object]:
    return {
        "id": "IR-TR-01",
        "input": "Handle the placeholder incident.",
        "available_tools": ["get_ticket"],
        "expected_outcome": {
            "ticket_id": "T01",
            "classification": "outage",
            "severity": "SEV1",
            "evidence": ["ticket"],
            "escalation_path": "pager",
            "final_status": "resolved",
            "required_updates": {"priority": "SEV1", "owner": "pager"},
        },
        "invariants": ["ticket_loaded"],
        "split": "train",
        "max_calls": 1,
    }


def _trajectory_payload() -> dict[str, object]:
    return {
        "experiment_id": "exp-20000101T000000Z-00000000",
        "task_id": "IR-TR-01",
        "skill_version": "v001",
        "messages": [{"role": "assistant", "content": "done"}],
        "tool_calls": [
            {
                "tool": "get_ticket",
                "arguments": {"ticket_id": "T01"},
                "result": {"ticket": {"id": "T01"}},
                "timestamp": "2000-01-01T00:00:01.000Z",
            }
        ],
        "final_output": {"ticket_id": "T01"},
        "tokens": 10,
        "latency_ms": 5,
        "outcome": "success",
    }


def _run_payload() -> dict[str, object]:
    verification = {"success": True, "checks": {"schema_valid": True}, "evidence": {}}
    return {
        "run_id": "run-0000000000000000",
        "experiment_id": "exp-20000101T000000Z-00000000",
        "task_id": "IR-TR-01",
        "split": "train",
        "condition_name": "seed",
        "skill_version": "v001",
        "run_slot": 0,
        "seed": 1729,
        "outcome": "success",
        "success": True,
        "input_tokens": 5,
        "output_tokens": 5,
        "total_tokens": 10,
        "latency_ms": 5,
        "estimated_cost_usd": 0.0,
        "trajectory": _trajectory_payload(),
        "verification": verification,
    }


def test_task_accepts_frozen_shape() -> None:
    task = Task.model_validate(_task_payload())
    assert task.id == "IR-TR-01"
    assert task.split is Split.TRAIN
    assert task.max_calls == 1
    assert Task.model_validate_json(task.model_dump_json()) == task

    tool_call = ToolCall.model_validate(_trajectory_payload()["tool_calls"][0])
    assert ToolCall.model_validate_json(tool_call.model_dump_json()) == tool_call

    trajectory = Trajectory.model_validate(_trajectory_payload())
    assert trajectory.messages == [{"role": "assistant", "content": "done"}]
    assert trajectory.tool_calls == [tool_call]
    assert trajectory.final_output == {"ticket_id": "T01"}
    assert Trajectory.model_validate_json(trajectory.model_dump_json()) == trajectory

    verification = VerificationResult(success=True, checks={"schema_valid": True}, evidence={})
    assert VerificationResult.model_validate_json(verification.model_dump_json()) == verification

    candidate = CandidateSkill(
        candidate_id="cand-000000000000",
        parent_version="v001",
        candidate_version="v002",
        generation=1,
        failure_analysis="The placeholder procedure missed a required step.",
        procedural_change="Add the required step before escalation.",
        candidate_markdown="# Procedure",
        rationale="The change addresses the observed failure.",
    )
    assert candidate.status is MutationStatus.PROPOSED
    assert CandidateSkill.model_validate_json(candidate.model_dump_json()) == candidate

    decision = PromotionDecision(
        candidate_id="cand-000000000000",
        parent_version="v001",
        candidate_version="v002",
        decision="reject",
        reason_codes=["inconclusive"],
    )
    assert decision.decision is Decision.REJECT
    assert decision.reason_codes == [RejectionReason.INCONCLUSIVE]
    assert PromotionDecision.model_validate_json(decision.model_dump_json()) == decision

    run = RunRecord.model_validate(_run_payload())
    assert run.condition_name is Condition.SEED
    assert run.outcome is Outcome.SUCCESS
    assert RunRecord.model_validate_json(run.model_dump_json()) == run


def test_task_rejects_extra_fields() -> None:
    payload = _task_payload()
    payload["unexpected"] = "not part of the frozen task shape"

    with pytest.raises(ValidationError):
        Task.model_validate(payload)


def test_id_and_version_grammars() -> None:
    for invalid_id in ("IR-XX-01", "IR-TR-1", "IR-TR-001"):
        payload = _task_payload()
        payload["id"] = invalid_id
        with pytest.raises(ValidationError):
            Task.model_validate(payload)

    for field, invalid_value in (
        ("experiment_id", "exp-invalid"),
        ("task_id", "IR-XX-01"),
        ("skill_version", "v1"),
    ):
        payload = _trajectory_payload()
        payload[field] = invalid_value
        with pytest.raises(ValidationError):
            Trajectory.model_validate(payload)

    for field, invalid_value in (
        ("candidate_id", "cand-invalid"),
        ("parent_version", "v1"),
        ("candidate_version", "version-002"),
    ):
        payload: dict[str, object] = {
            "candidate_id": "cand-000000000000",
            "parent_version": "v001",
            "candidate_version": "v002",
            "generation": 1,
            "failure_analysis": "failure",
            "procedural_change": "change",
            "candidate_markdown": "markdown",
            "rationale": "reason",
        }
        payload[field] = invalid_value
        with pytest.raises(ValidationError):
            CandidateSkill.model_validate(payload)

    for field, invalid_value in (
        ("run_id", "run-invalid"),
        ("experiment_id", "exp-invalid"),
        ("task_id", "IR-XX-01"),
        ("skill_version", "v1"),
    ):
        payload = _run_payload()
        payload[field] = invalid_value
        with pytest.raises(ValidationError):
            RunRecord.model_validate(payload)

    task_payload = _task_payload()
    task_payload["split"] = "validation-data"
    with pytest.raises(ValidationError):
        Task.model_validate(task_payload)

    trajectory_payload = _trajectory_payload()
    trajectory_payload["outcome"] = "unknown"
    with pytest.raises(ValidationError):
        Trajectory.model_validate(trajectory_payload)

    run_payload = _run_payload()
    run_payload["condition_name"] = "unknown"
    with pytest.raises(ValidationError):
        RunRecord.model_validate(run_payload)
