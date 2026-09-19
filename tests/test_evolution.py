import json
from pathlib import Path
from typing import Any

import pytest

from skill_lab.agent import AgentConfig
from skill_lab.experiment import evolve
from skill_lab.mock_model import ModelResponse, ScriptedMockModel
from skill_lab.models import Decision, MutationStatus, Split
from skill_lab.promotion import PromotionPolicy
from skill_lab.skills import load_skill
from skill_lab.tasks import load_tasks

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "datasets" / "incident_tasks.json"
FIXTURES = ROOT / "datasets" / "tool_world.json"
SKILLS = ROOT / "skills"
EXPERIMENT_ID = "exp-20000101T000000Z-00000000"
POLICY = PromotionPolicy(regression_threshold=0.0, cost_tolerance=1.10)
PRICING = {"mock-incident-v1": {"input_per_million_usd": 0.0, "output_per_million_usd": 0.0}}


def _inputs() -> tuple[list[Any], dict[str, Any]]:
    return (
        load_tasks(DATASET),
        json.loads(FIXTURES.read_text(encoding="utf-8")),
    )


def _evolve(tmp_path: Path, *, model: Any, mode: str, generations: int = 2) -> Any:
    tasks, fixtures = _inputs()
    return evolve(
        tasks=[task for task in tasks if task.split is not Split.TEST],
        fixtures=fixtures,
        model=model,
        agent_config=AgentConfig(max_steps=8, token_budget=2400),
        experiment_id=EXPERIMENT_ID,
        mode=mode,
        generations=generations,
        runs_per_task=1,
        parent_skill=load_skill(SKILLS, "incident-response", "v001"),
        skills_root=tmp_path,
        policy=POLICY,
        pricing=PRICING,
    )


class _MutationFailureModel:
    model_id = "mock-incident-v1"

    def __init__(self) -> None:
        self.delegate = ScriptedMockModel(seed=1729)
        self.mutation_calls = 0

    def complete(self, request: dict[str, Any]) -> ModelResponse:
        if request.get("kind") == "mutation":
            self.mutation_calls += 1
            raise RuntimeError("placeholder mutation failure")
        return self.delegate.complete(request)


class _TrainFailureCaptureModel:
    model_id = "mock-incident-v1"

    def __init__(self) -> None:
        self.delegate = ScriptedMockModel(seed=1729)
        self.mutation_requests: list[dict[str, Any]] = []

    def complete(self, request: dict[str, Any]) -> ModelResponse:
        if request.get("kind") == "mutation":
            self.mutation_requests.append(request)
            return self.delegate.complete(request)
        if request.get("task_id") == "IR-TR-01":
            return ModelResponse(
                content=json.dumps({"action": "unsupported"}, sort_keys=True),
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                latency_ms=1,
            )
        return self.delegate.complete(request)


class _MutationExchangeModel:
    model_id = "mock-incident-v1"

    def __init__(self) -> None:
        self.delegate = ScriptedMockModel(seed=1729)
        self.requests: list[dict[str, Any]] = []
        self.responses: list[str | None] = []

    def complete(self, request: dict[str, Any]) -> ModelResponse:
        response = self.delegate.complete(request)
        if request.get("kind") == "mutation":
            self.requests.append(request)
            self.responses.append(response.content)
        return response


def test_verified_evolution_has_promote_and_reject(tmp_path: Path) -> None:
    result = _evolve(tmp_path, model=ScriptedMockModel(seed=1729), mode="verified")

    assert len(result.generations) == 2
    assert [record.decision for record in result.generations] == [
        Decision.PROMOTE,
        Decision.REJECT,
    ]
    assert [record.mutation_status for record in result.generations] == [
        MutationStatus.PROMOTED,
        MutationStatus.REJECTED,
    ]
    assert result.final_version == "v002"
    assert load_skill(tmp_path, "incident-response", "v002").status == "promoted"
    assert load_skill(tmp_path, "incident-response", "v003").status == "rejected"
    assert result.model_validate_json(result.model_dump_json()) == result


def test_naive_evolution_auto_replaces(tmp_path: Path) -> None:
    result = _evolve(tmp_path, model=ScriptedMockModel(seed=1729), mode="naive")

    assert len(result.generations) == 2
    assert [record.decision for record in result.generations] == [
        Decision.NAIVE_REPLACE,
        Decision.NAIVE_REPLACE,
    ]
    assert all(
        record.mutation_status is MutationStatus.NAIVE_REPLACED for record in result.generations
    )
    assert result.final_version == "v003"
    assert load_skill(tmp_path, "incident-response", "v002").status == "naive_replaced"
    assert load_skill(tmp_path, "incident-response", "v003").status == "naive_replaced"


def test_generation_errors_are_not_retried(tmp_path: Path) -> None:
    model = _MutationFailureModel()

    result = _evolve(tmp_path, model=model, mode="naive")

    assert model.mutation_calls == 2
    assert len(result.generations) == 2
    assert all(
        record.mutation_status is MutationStatus.GENERATION_ERROR for record in result.generations
    )
    assert result.final_version == "v001"


def test_only_train_failures_are_selected(tmp_path: Path) -> None:
    model = _TrainFailureCaptureModel()

    result = _evolve(tmp_path, model=model, mode="naive", generations=1)

    assert result.generations[0].train_failure_task_ids == ["IR-TR-01"]
    request = model.mutation_requests[0]
    packets = request["train_failures"]
    assert [packet["task_id"] for packet in packets] == ["IR-TR-01"]
    assert all(packet["task_id"].startswith("IR-TR-") for packet in packets)
    assert result.final_version == "v002"


def test_experiment_result_carries_all_non_test_runs(tmp_path: Path) -> None:
    result = _evolve(tmp_path, model=ScriptedMockModel(seed=1729), mode="verified")

    assert result.runs
    assert len(result.runs) == len({run.run_id for run in result.runs})
    assert all(run.split in {Split.TRAIN, Split.VALIDATION} for run in result.runs)
    assert all(run.split is not Split.TEST for run in result.runs)
    run_keys = [
        (run.task_id, run.skill_version or "", run.condition_name.value, run.run_slot)
        for run in result.runs
    ]
    assert run_keys == sorted(
        (run.task_id, run.skill_version or "", run.condition_name.value, run.run_slot)
        for run in result.runs
    )


def test_mutation_exchanges_are_captured_verbatim(tmp_path: Path) -> None:
    model = _MutationExchangeModel()

    result = _evolve(tmp_path, model=model, mode="verified")

    assert len(result.prompt_records) == 2
    assert [record["generation"] for record in result.prompt_records] == [1, 2]
    assert [record["request"] for record in result.prompt_records] == model.requests
    assert [record["response_content"] for record in result.prompt_records] == model.responses
    assert [record["candidate_id"] for record in result.prompt_records] == [
        generation.candidate_id for generation in result.generations
    ]


def test_skill_lineage_retains_rejected_versions(tmp_path: Path) -> None:
    result = _evolve(tmp_path, model=ScriptedMockModel(seed=1729), mode="verified")

    assert [skill.version for skill in result.skill_versions] == ["v001", "v002", "v003"]
    assert [skill.status for skill in result.skill_versions] == [
        "baseline",
        "promoted",
        "rejected",
    ]


def test_evolve_refuses_test_tasks(tmp_path: Path) -> None:
    tasks, fixtures = _inputs()

    with pytest.raises(
        ValueError,
        match="evolve receives training/validation tasks only; test data is read exclusively by "
        "run_held_out",
    ):
        evolve(
            tasks=tasks,
            fixtures=fixtures,
            model=ScriptedMockModel(seed=1729),
            agent_config=AgentConfig(max_steps=8, token_budget=2400),
            experiment_id=EXPERIMENT_ID,
            mode="naive",
            generations=1,
            runs_per_task=1,
            parent_skill=load_skill(SKILLS, "incident-response", "v001"),
            skills_root=tmp_path,
        )
