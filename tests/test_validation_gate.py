from pathlib import Path
from typing import Any

from skill_lab.agent import AgentConfig
from skill_lab.experiment import evaluate_validation_gate
from skill_lab.mock_model import ModelResponse, ScriptedMockModel
from skill_lab.models import Decision
from skill_lab.promotion import PromotionPolicy
from skill_lab.skills import Skill, load_skill, write_candidate
from skill_lab.tasks import load_tasks

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "datasets" / "incident_tasks.json"
FIXTURES = ROOT / "datasets" / "tool_world.json"
SKILLS = ROOT / "skills"
EXPERIMENT_ID = "exp-20000101T000000Z-00000000"
POLICY = PromotionPolicy(regression_threshold=0.0, cost_tolerance=1.10)


class _BoundarySpy:
    def __init__(self, seed: int = 1729) -> None:
        self.delegate = ScriptedMockModel(seed=seed)
        self.requests: list[dict[str, Any]] = []
        self.mutation_requests: list[dict[str, Any]] = []

    def complete(self, request: dict[str, Any]) -> ModelResponse:
        self.requests.append(request)
        if request.get("kind") != "agent":
            self.mutation_requests.append(request)
            raise AssertionError("validation evidence reached a mutation request")
        return self.delegate.complete(request)


def _candidate(root: Path, parent: Skill, version: str) -> Skill:
    write_candidate(
        root,
        name=parent.name,
        version=version,
        parent=parent,
        candidate_markdown=parent.markdown,
        rationale=f"Placeholder rationale for {version}.",
    )
    return load_skill(root, parent.name, version)


def _run_gate(root: Path, parent: Skill, candidate: Skill, model: Any) -> Any:
    return evaluate_validation_gate(
        tasks=load_tasks(DATASET),
        fixtures=FIXTURES,
        model=model,
        agent_config=AgentConfig(max_steps=8, token_budget=2400),
        experiment_id=EXPERIMENT_ID,
        parent_skill=parent,
        candidate_skill=candidate,
        skills_root=root,
        policy=POLICY,
        runs_per_task=1,
        base_seed=1729,
        pricing={
            "mock-incident-v1": {
                "input_per_million_usd": 0.0,
                "output_per_million_usd": 0.0,
            }
        },
    )


def test_validation_gate_promotes_mock_v002(tmp_path: Path) -> None:
    parent = load_skill(SKILLS, "incident-response", "v001")
    candidate = _candidate(tmp_path, parent, "v002")

    decision = _run_gate(tmp_path, parent, candidate, ScriptedMockModel(seed=1729))

    assert decision.decision is Decision.PROMOTE
    assert decision.parent_version == "v001"
    assert decision.candidate_version == "v002"
    assert load_skill(tmp_path, "incident-response", "v002").status == "promoted"
    assert load_skill(SKILLS, "incident-response", "v001").status == "baseline"


def test_validation_gate_rejects_mock_v003(tmp_path: Path) -> None:
    first_parent = load_skill(SKILLS, "incident-response", "v001")
    promoted_parent = _candidate(tmp_path, first_parent, "v002")
    candidate = _candidate(tmp_path, promoted_parent, "v003")

    decision = _run_gate(tmp_path, promoted_parent, candidate, ScriptedMockModel(seed=1729))

    assert decision.decision is Decision.REJECT
    assert decision.parent_version == "v002"
    assert decision.candidate_version == "v003"
    assert load_skill(tmp_path, "incident-response", "v003").status == "rejected"
    assert "IR-VA-03" in str(decision.evidence)


def test_validation_evidence_cannot_reach_mutation(tmp_path: Path) -> None:
    parent = load_skill(SKILLS, "incident-response", "v001")
    candidate = _candidate(tmp_path, parent, "v002")
    spy = _BoundarySpy()

    _run_gate(tmp_path, parent, candidate, spy)

    assert spy.requests
    assert spy.mutation_requests == []
    assert all(request["kind"] == "agent" for request in spy.requests)
    assert all(request["task"]["split"] == "validation" for request in spy.requests)
    assert all("train_failures" not in request for request in spy.requests)
