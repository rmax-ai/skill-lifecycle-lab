import json
from pathlib import Path
from typing import Any

import pytest

import skill_lab.experiment as experiment_module
from skill_lab.agent import AgentConfig
from skill_lab.experiment import ablate, evolve
from skill_lab.mock_model import ModelResponse, ScriptedMockModel
from skill_lab.models import Split
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
MARKERS = ("POISON-VALIDATION", "POISON-TEST")


def _poisoned_tasks() -> list[Any]:
    return [_poison_task(task) for task in load_tasks(DATASET)]


def _poison_task(task: Any) -> Any:
    if task.split is Split.TRAIN:
        return task
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


def _fixtures() -> dict[str, Any]:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


def _run_ablation(
    tmp_path: Path,
    model: Any,
    *,
    store: ExperimentStore | None = None,
) -> Any:
    return ablate(
        tasks=_poisoned_tasks(),
        fixtures=_fixtures(),
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


def _serialized(value: object) -> str:
    return json.dumps(value, sort_keys=True)


class _LeakageSpy:
    model_id = "mock-incident-v1"

    def __init__(self) -> None:
        self.delegate = ScriptedMockModel(seed=1729)
        self.requests: list[dict[str, Any]] = []
        self.mutation_requests: list[dict[str, Any]] = []
        self.mutation_responses: list[str] = []

    def complete(self, request: dict[str, Any]) -> ModelResponse:
        captured = json.loads(json.dumps(request, sort_keys=True))
        self.requests.append(captured)
        if request.get("kind") == "agent" and request.get("task_id") == "IR-TR-01":
            return ModelResponse(
                content=json.dumps({"action": "unsupported"}, sort_keys=True),
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                latency_ms=1,
            )
        response = self.delegate.complete(request)
        if request.get("kind") == "mutation":
            self.mutation_requests.append(captured)
            self.mutation_responses.append(response.content)
            return response

        task = request.get("task")
        if not isinstance(task, dict):
            return response
        split = task.get("split")
        if split not in {"validation", "test"}:
            return response
        marker = "POISON-TEST" if split == "test" else "POISON-VALIDATION"
        payload = json.loads(response.content)
        payload["trajectory_marker"] = marker
        return response.model_copy(update={"content": json.dumps(payload, sort_keys=True)})


def _selection_spy(monkeypatch: pytest.MonkeyPatch) -> tuple[list[list[Any]], list[Any]]:
    original = experiment_module.select_train_failures
    selected_runs: list[list[Any]] = []
    failure_packets: list[Any] = []

    def capture(train_runs: Any, task_inputs: Any = None) -> list[Any]:
        selected_runs.append(list(train_runs))
        packets = original(train_runs, task_inputs=task_inputs)
        failure_packets.extend(packets)
        return packets

    monkeypatch.setattr(experiment_module, "select_train_failures", capture)
    return selected_runs, failure_packets


def test_poison_never_reaches_mutation_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _LeakageSpy()
    selected_runs, failure_packets = _selection_spy(monkeypatch)
    with ExperimentStore(tmp_path / "evidence.sqlite3") as store:
        results = _run_ablation(tmp_path, model, store=store)
        mutation_rows = store.connection.execute(
            "SELECT * FROM mutations ORDER BY candidate_id"
        ).fetchall()

        assert selected_runs
        assert all(run.split is Split.TRAIN for batch in selected_runs for run in batch)
        assert all(run.task_id.startswith("IR-TR-") for batch in selected_runs for run in batch)
        assert failure_packets

        mutation_requests = [_serialized(request) for request in model.mutation_requests]
        prompt_records = [
            _serialized(record) for result in results.values() for record in result.prompt_records
        ]
        mutation_responses = list(model.mutation_responses)
        candidate_markdown = [
            skill.markdown
            for result in results.values()
            for skill in result.skill_versions
            if skill.version != "v001"
        ]
        persisted_mutations = [_serialized(dict(row)) for row in mutation_rows]
        serialized_packets = [
            _serialized(packet.model_dump(mode="json")) for packet in failure_packets
        ]

        for marker in MARKERS:
            assert all(marker not in value for value in mutation_requests)
            assert all(marker not in value for value in prompt_records)
            assert all(marker not in value for value in mutation_responses)
            assert all(marker not in value for value in candidate_markdown)
            assert all(marker not in value for value in persisted_mutations)
            assert all(marker not in value for value in serialized_packets)

        validation_requests = [
            request
            for request in model.requests
            if request.get("task", {}).get("split") == "validation"
        ]
        held_out_requests = [
            request for request in model.requests if request.get("task", {}).get("split") == "test"
        ]
        assert validation_requests
        assert held_out_requests
        assert any("POISON-VALIDATION" in _serialized(request) for request in validation_requests)
        assert any("POISON-TEST" in _serialized(request) for request in held_out_requests)

        stored_run_rows = store.connection.execute(
            "SELECT split, trajectory_json, verification_json FROM runs ORDER BY run_id"
        ).fetchall()
        assert any(
            row["split"] == "validation"
            and "POISON-VALIDATION" in f"{row['trajectory_json']}{row['verification_json']}"
            for row in stored_run_rows
        )
        assert any(
            row["split"] == "test"
            and "POISON-TEST" in f"{row['trajectory_json']}{row['verification_json']}"
            for row in stored_run_rows
        )


def test_test_split_unreachable_before_held_out_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _LeakageSpy()
    phase_timestamps: list[int] = []
    original = experiment_module.run_held_out

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        phase_timestamps.append(len(model.requests))
        return original(*args, **kwargs)

    monkeypatch.setattr(experiment_module, "run_held_out", wrapped)
    _run_ablation(tmp_path, model)

    assert phase_timestamps
    first_held_out_request = phase_timestamps[0]
    before_held_out = model.requests[:first_held_out_request]
    after_held_out = model.requests[first_held_out_request:]
    assert all(request.get("task", {}).get("split") != "test" for request in before_held_out)
    test_requests = [
        request for request in after_held_out if request.get("task", {}).get("split") == "test"
    ]
    assert test_requests
    assert any("POISON-TEST" in _serialized(request) for request in test_requests)


def test_evolve_guard_rejects_test_tasks(tmp_path: Path) -> None:
    test_task = next(task for task in load_tasks(DATASET) if task.split is Split.TEST)

    class _NoCallModel:
        model_id = "mock-incident-v1"

        def __init__(self) -> None:
            self.calls = 0

        def complete(self, request: dict[str, Any]) -> ModelResponse:
            self.calls += 1
            raise AssertionError(f"unexpected model call: {request}")

    model = _NoCallModel()
    with pytest.raises(
        ValueError,
        match="evolve receives training/validation tasks only; test data is read exclusively",
    ):
        evolve(
            tasks=[test_task],
            fixtures=_fixtures(),
            model=model,
            agent_config=AgentConfig(max_steps=8, token_budget=2400),
            experiment_id=EXPERIMENT_ID,
            mode="naive",
            generations=1,
            runs_per_task=1,
            parent_skill=load_skill(SKILLS, "incident-response", "v001"),
            skills_root=tmp_path,
        )
    assert model.calls == 0
