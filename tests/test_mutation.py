import json
from pathlib import Path
from typing import Any

import pytest

from skill_lab.mock_model import ModelResponse, ScriptedMockModel
from skill_lab.models import MutationStatus, RunRecord
from skill_lab.mutation import (
    CandidateResponseError,
    MutationConfig,
    TrainFailurePacket,
    propose_mutation,
    select_train_failures,
)
from skill_lab.skills import Skill, load_skill
from skill_lab.storage import ExperimentStore

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
EXPERIMENT_ID = "exp-20000101T000000Z-00000000"


class CapturingModel:
    model_id = "placeholder-model"

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.requests: list[dict[str, Any]] = []

    def complete(self, request: dict[str, Any]) -> ModelResponse:
        self.requests.append(request)
        content = json.dumps(self.payload, sort_keys=True)
        return ModelResponse(
            model=self.model_id,
            content=content,
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
            latency_ms=1,
        )


def _skill() -> Skill:
    return load_skill(SKILLS, "incident-response", "v001")


def _packet(task_id: str, *, split: str = "train") -> TrainFailurePacket:
    return TrainFailurePacket(
        split=split,
        task_id=task_id,
        task_input=f"Handle {task_id}.",
        trajectory={
            "experiment_id": EXPERIMENT_ID,
            "task_id": task_id,
            "skill_version": "v001",
            "messages": [{"role": "user", "content": f"Handle {task_id}."}],
            "tool_calls": [],
            "final_output": {},
            "tokens": 1,
            "latency_ms": 1,
            "outcome": "failure",
        },
        verification={"success": False, "checks": {}, "evidence": {}},
    )


def _run(task_id: str, slot: int, success: bool) -> RunRecord:
    packet = _packet(task_id)
    trajectory = packet.trajectory
    return RunRecord(
        run_id=f"run-{slot + 1:016x}",
        experiment_id=EXPERIMENT_ID,
        task_id=task_id,
        split="train",
        condition_name="seed",
        skill_version="v001",
        run_slot=slot,
        seed=1729 + slot,
        outcome="success" if success else "failure",
        success=success,
        input_tokens=1,
        output_tokens=1,
        total_tokens=2,
        latency_ms=1,
        estimated_cost_usd=0.0,
        trajectory=trajectory,
        verification=packet.verification,
    )


def _response(markdown: str | None = None) -> dict[str, str]:
    return {
        "failure_analysis": "The escalation procedure omitted a required gate.",
        "procedural_change": "Request approval before an approval-gated escalation.",
        "candidate_markdown": markdown
        or "---\n"
        "name: incident-response\n"
        "version: 2\n"
        "description: End-to-end procedure for handling customer incident tickets.\n"
        "---\n"
        "\n# Candidate procedure\n",
        "rationale": "The change makes the missing gate explicit and reviewable.",
    }


def test_prompt_contains_train_failures_only() -> None:
    failures = select_train_failures(
        [_run("IR-TR-02", 0, False), _run("IR-TR-01", 0, False), _run("IR-TR-03", 0, True)]
    )
    model = CapturingModel(_response())

    candidate = propose_mutation(_skill(), failures, model, MutationConfig())

    assert [packet.task_id for packet in failures] == ["IR-TR-01", "IR-TR-02"]
    prompt_json = json.dumps(model.requests[0], sort_keys=True)
    assert candidate.status is MutationStatus.PROPOSED
    assert "IR-TR-01" in prompt_json
    assert "IR-TR-02" in prompt_json
    assert "IR-TR-03" not in prompt_json
    assert "IR-VA-" not in prompt_json
    assert "IR-TE-" not in prompt_json
    assert "validation-only" not in prompt_json
    assert "test-only" not in prompt_json


def test_non_train_packet_is_rejected() -> None:
    with pytest.raises(ValueError, match="train"):
        _packet("IR-VA-01", split="validation")
    with pytest.raises(ValueError, match="train"):
        _packet("IR-TE-01", split="test")

    with pytest.raises(ValueError, match="train"):
        select_train_failures([_run("IR-TR-01", 0, False).model_copy(update={"split": "test"})])


def test_candidate_response_contract() -> None:
    skill = _skill()
    model = CapturingModel(_response())

    candidate = propose_mutation(skill, [_packet("IR-TR-01")], model, MutationConfig())

    assert candidate.failure_analysis == _response()["failure_analysis"]
    assert candidate.procedural_change == _response()["procedural_change"]
    assert candidate.rationale == _response()["rationale"]
    assert candidate.parent_version == skill.version
    assert candidate.candidate_version == "v002"
    assert candidate.generation == 1
    assert candidate.status is MutationStatus.PROPOSED
    assert CandidateResponseError
    assert candidate.model_validate_json(candidate.model_dump_json()) == candidate

    invalid_model = CapturingModel({**_response(), "status": "promoted"})
    with pytest.raises(CandidateResponseError, match="exactly four"):
        propose_mutation(skill, [_packet("IR-TR-01")], invalid_model, MutationConfig())


def test_mutation_cannot_write_skill_status() -> None:
    skill = _skill()
    original_metadata = skill.metadata.model_dump(mode="json")
    packet = _packet("IR-TR-01")

    candidate = propose_mutation(
        skill,
        [packet],
        ScriptedMockModel(seed=1729),
        MutationConfig(),
    )

    assert candidate.status is MutationStatus.PROPOSED
    assert skill.status == "baseline"
    assert skill.metadata.model_dump(mode="json") == original_metadata


def test_stored_rows_do_not_feed_mutation(tmp_path: Path) -> None:
    run = _run("IR-TR-01", 0, False).model_copy(
        update={
            "trajectory": _packet("IR-TR-01").trajectory.model_copy(
                update={
                    "messages": [
                        {
                            "role": "user",
                            "content": "Handle IR-TR-01. POISON-VALIDATION",
                        }
                    ]
                }
            )
        }
    )
    with ExperimentStore(tmp_path / "evidence.sqlite3") as store:
        store.insert_experiment(
            experiment_id=EXPERIMENT_ID,
            created_at="2000-01-01T00:00:00.000Z",
            mode="verified",
            config_json={},
            git_commit="placeholder",
            dataset_sha256="placeholder",
            model_id="placeholder-model",
            seed=1729,
            runs_per_task=1,
            status="running",
        )
        store.insert_run(run)
        stored_runs = store.list_runs(EXPERIMENT_ID)

    assert "POISON-VALIDATION" in json.dumps(
        stored_runs[0].trajectory.model_dump(mode="json"),
        sort_keys=True,
    )
    with pytest.raises(ValueError, match="validation or test evidence"):
        select_train_failures(stored_runs)
