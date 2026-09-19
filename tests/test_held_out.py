import json
from pathlib import Path
from typing import Any

from skill_lab.agent import AgentConfig
from skill_lab.experiment import ablate
from skill_lab.mock_model import ModelResponse, ScriptedMockModel
from skill_lab.models import Condition, Split
from skill_lab.promotion import PromotionPolicy
from skill_lab.skills import load_skill
from skill_lab.storage import ExperimentStore
from skill_lab.tasks import load_tasks

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "datasets" / "incident_tasks.json"
FIXTURES = ROOT / "datasets" / "tool_world.json"
SKILLS = ROOT / "skills"
EXPERIMENT_ID = "exp-20000101T000000Z-00000000"
POLICY = PromotionPolicy(regression_threshold=0.0, cost_tolerance=1.10)
PRICING = {"mock-incident-v1": {"input_per_million_usd": 0.0, "output_per_million_usd": 0.0}}


def _inputs(*, poison: bool = False) -> tuple[list[Any], dict[str, Any]]:
    tasks = load_tasks(DATASET)
    if poison:
        tasks = [
            _poison_task(task) if task.split in {Split.TEST, Split.VALIDATION} else task
            for task in tasks
        ]
    return tasks, json.loads(FIXTURES.read_text(encoding="utf-8"))


def _poison_task(task: Any) -> Any:
    marker = "POISON-TEST" if task.split is Split.TEST else "POISON-VALIDATION"
    expected_outcome = dict(task.expected_outcome)
    expected_outcome["evidence"] = [*expected_outcome["evidence"], marker]
    return task.model_copy(
        update={
            "input": f"{marker} {task.input}",
            "expected_outcome": expected_outcome,
            "invariants": [*task.invariants, marker],
        }
    )


def _run(
    tmp_path: Path,
    *,
    model: Any,
    poison: bool = False,
    store: ExperimentStore | None = None,
) -> Any:
    tasks, fixtures = _inputs(poison=poison)
    return ablate(
        tasks=tasks,
        fixtures=fixtures,
        model=model,
        agent_config=AgentConfig(max_steps=8, token_budget=2400),
        experiment_id=EXPERIMENT_ID,
        generations=2,
        runs_per_task=1,
        parent_skill=load_skill(SKILLS, "incident-response", "v001"),
        skills_root=tmp_path / "roots",
        policy=POLICY,
        pricing=PRICING,
        store=store,
    )


class _RequestSpy:
    model_id = "mock-incident-v1"

    def __init__(self) -> None:
        self.delegate = ScriptedMockModel(seed=1729)
        self.requests: list[dict[str, Any]] = []

    def complete(self, request: dict[str, Any]) -> ModelResponse:
        self.requests.append(request)
        response = self.delegate.complete(request)
        task = request.get("task")
        if request.get("kind") != "agent" or not isinstance(task, dict):
            return response
        split = task.get("split")
        if split not in {"validation", "test"}:
            return response
        marker = "POISON-TEST" if split == "test" else "POISON-VALIDATION"
        payload = json.loads(response.content)
        payload["trajectory_marker"] = marker
        return response.model_copy(update={"content": json.dumps(payload, sort_keys=True)})


def test_held_out_runs_three_conditions_for_both_modes(tmp_path: Path) -> None:
    results = _run(tmp_path, model=ScriptedMockModel(seed=1729))

    assert set(results) == {"verified", "naive"}
    expected = [Condition.NO_SKILL, Condition.SEED]
    for mode, result in results.items():
        assert result.mode == mode
        assert [summary.condition_name for summary in result.held_out_summaries] == [
            *expected,
            Condition.EVOLVED_VERIFIED if mode == "verified" else Condition.EVOLVED_NAIVE,
        ]
        assert all(summary.split is Split.TEST for summary in result.held_out_summaries)
        assert all(summary.run_count == 6 for summary in result.held_out_summaries)
        assert len(result.held_out_runs) == 18
        assert (tmp_path / "roots" / mode).exists()


def test_test_data_is_not_touched_before_final_evaluation(tmp_path: Path) -> None:
    model = _RequestSpy()
    with ExperimentStore(tmp_path / "evidence.sqlite3") as store:
        results = _run(tmp_path, model=model, poison=True, store=store)

        serialized = [json.dumps(request, sort_keys=True) for request in model.requests]
        first_test_request = next(
            index
            for index, request in enumerate(model.requests)
            if request.get("task", {}).get("split") == "test"
        )
        assert all("POISON-TEST" not in item for item in serialized[:first_test_request])
        assert any("POISON-TEST" in item for item in serialized[first_test_request:])
        assert all(
            "POISON-TEST" not in item and "POISON-VALIDATION" not in item
            for item in serialized
            if '"kind": "mutation"' in item
        )

        validation_requests = [
            request
            for request in model.requests
            if request.get("task", {}).get("split") == "validation"
        ]
        assert validation_requests
        assert any(
            "POISON-VALIDATION" in json.dumps(request["task"]["expected_outcome"], sort_keys=True)
            for request in validation_requests
        )
        assert any(
            "POISON-VALIDATION" in json.dumps(request["task"]["invariants"], sort_keys=True)
            for request in validation_requests
        )

        stored_rows = store.connection.execute(
            "SELECT split, trajectory_json, verification_json FROM runs ORDER BY run_id"
        ).fetchall()
        assert any(
            row["split"] == "validation"
            and "POISON-VALIDATION" in f"{row['trajectory_json']}{row['verification_json']}"
            for row in stored_rows
        )
        assert any(
            row["split"] == "test"
            and "POISON-TEST" in f"{row['trajectory_json']}{row['verification_json']}"
            for row in stored_rows
        )
        assert any(
            "trajectory_marker" in row["trajectory_json"]
            for row in stored_rows
            if row["split"] in {"validation", "test"}
        )
    assert all(result.held_out_summaries for result in results.values())


def test_training_vs_held_out_lift_reported(tmp_path: Path) -> None:
    results = _run(tmp_path, model=ScriptedMockModel(seed=1729))

    for result in results.values():
        expected = (
            result.held_out_summaries[-1].success_rate - result.train_summaries[-1].success_rate
        )
        assert result.train_vs_held_out_lift == expected
        assert result.train_vs_heldout_lift == expected
        assert result["train_vs_heldout_lift"] == expected
