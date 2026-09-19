"""Deterministic experiment evaluation for baseline and later workflows."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skill_lab.agent import AgentConfig, ChatModel, run_agent
from skill_lab.metrics import EvaluationSummary, estimate_cost, summarize_runs
from skill_lab.models import (
    CandidateSkill,
    Condition,
    Outcome,
    PromotionDecision,
    RunRecord,
    Split,
    Task,
    Trajectory,
    VerificationResult,
)
from skill_lab.promotion import PromotionPolicy, apply_promotion, decide_promotion
from skill_lab.skills import Skill
from skill_lab.storage import ExperimentStore
from skill_lab.tasks import tasks_for_split
from skill_lab.tools import ToolEnvironment
from skill_lab.verifier import verify


@dataclass(frozen=True)
class _Usage:
    """Usage metadata captured without changing the agent contract."""

    input_tokens: int
    output_tokens: int
    model_id: str | None


class _UsageCapturingModel:
    """Forward model calls while retaining token and model metadata."""

    def __init__(self, model: ChatModel) -> None:
        self._model = model
        self.usages: list[_Usage] = []

    def complete(self, request: dict[str, Any]) -> Any:
        response = self._model.complete(request)
        self.usages.append(_usage_from_response(response, self._model))
        return response


class _RunCollector:
    """Collect evaluated runs while optionally forwarding them to a store."""

    def __init__(self, store: ExperimentStore | None) -> None:
        self.runs: list[RunRecord] = []
        self._store = store

    def insert_run(self, run: RunRecord) -> None:
        if self._store is not None:
            self._store.insert_run(run)
        self.runs.append(run)


def evaluate_condition(
    tasks: Sequence[Task],
    fixtures: Mapping[str, Any] | Path | str | None = None,
    model: ChatModel | None = None,
    agent_config: AgentConfig | None = None,
    *,
    experiment_id: str,
    split: Split | str = Split.TRAIN,
    condition_name: Condition | str = Condition.NO_SKILL,
    runs_per_task: int = 1,
    base_seed: int = 1729,
    store: ExperimentStore | None = None,
    skill_markdown: str | None = None,
    skill_version: str | None = None,
    pricing: Mapping[str, Any] | None = None,
    parent_runs: Sequence[RunRecord] | None = None,
    no_skill_runs: Sequence[RunRecord] | None = None,
    condition: Condition | str | None = None,
    config: Any | None = None,
    seed: int | None = None,
    runs: int | None = None,
    skill: Skill | str | None = None,
    experiment_store: ExperimentStore | None = None,
    fixture_data: Mapping[str, Any] | None = None,
) -> EvaluationSummary:
    """Evaluate one condition over a non-test split and persist every slot.

    Each task/slot is run exactly once with seed
    ``base_seed + task_index * 1000 + run_slot``. A fresh tool environment is
    created for each run, and failed runs are inserted before the next slot.
    """

    resolved_split = _split_value(split)
    if resolved_split is Split.TEST:
        raise ValueError("evaluate_condition cannot evaluate the test split")

    resolved_config = _resolve_config(config)
    if agent_config is None:
        agent_config = (
            resolved_config
            if isinstance(resolved_config, AgentConfig)
            else _config_value(resolved_config, "agent")
        )
    if agent_config is None:
        raise TypeError("agent_config or config.agent is required")
    if not isinstance(agent_config, AgentConfig):
        agent_config = AgentConfig.model_validate(agent_config)

    if isinstance(fixtures, (Path, str)):
        fixtures = _read_fixture_file(fixtures)
    if fixtures is None:
        fixtures = fixture_data
    if isinstance(fixtures, (Path, str)):
        fixtures = _read_fixture_file(fixtures)
    if fixtures is None:
        fixtures = _load_fixtures(resolved_config)
    if fixtures is None:
        raise TypeError("fixtures or fixture_data is required")

    if model is None:
        model = _config_model(resolved_config)
    if model is None:
        raise TypeError("model is required")

    if seed is not None:
        base_seed = seed
    if runs is not None:
        runs_per_task = runs
    if condition is not None:
        condition_name = condition
    if experiment_store is not None:
        store = experiment_store
    if pricing is None:
        pricing = _config_value(resolved_config, "pricing")
    if seed is None and resolved_config is not None:
        configured_seed = _config_value(resolved_config, "seed")
        if isinstance(configured_seed, int):
            base_seed = configured_seed

    resolved_condition = _condition_value(condition_name)
    if runs_per_task < 1:
        raise ValueError("runs_per_task must be at least 1")

    skill_markdown, skill_version = _resolve_skill(
        skill=skill,
        skill_markdown=skill_markdown,
        skill_version=skill_version,
        condition=resolved_condition,
    )
    selected_tasks = _indexed_tasks(tasks, resolved_split)
    run_records: list[RunRecord] = []
    for task_index, task in selected_tasks:
        for run_slot in range(runs_per_task):
            run_seed = base_seed + task_index * 1000 + run_slot
            run = _evaluate_slot(
                task=task,
                fixtures=fixtures,
                model=model,
                agent_config=agent_config,
                experiment_id=experiment_id,
                condition=resolved_condition,
                skill_markdown=skill_markdown,
                skill_version=skill_version,
                split=resolved_split,
                run_slot=run_slot,
                seed=run_seed,
                pricing=pricing,
            )
            if store is not None:
                store.insert_run(run)
            run_records.append(run)

    return summarize_runs(
        run_records,
        pricing,
        parent_runs=parent_runs,
        no_skill_runs=no_skill_runs,
    )


def evaluate_validation_gate(
    tasks: Sequence[Task],
    fixtures: Mapping[str, Any] | Path | str | None = None,
    model: ChatModel | None = None,
    agent_config: AgentConfig | None = None,
    *,
    experiment_id: str,
    parent_skill: Skill,
    candidate_skill: Skill | CandidateSkill,
    skills_root: Path | str | None = None,
    policy: PromotionPolicy | Mapping[str, Any] | Any | None = None,
    runs_per_task: int = 1,
    base_seed: int = 1729,
    store: ExperimentStore | None = None,
    pricing: Mapping[str, Any] | None = None,
    config: Any | None = None,
    fixture_data: Mapping[str, Any] | None = None,
) -> PromotionDecision:
    """Evaluate parent and candidate on validation tasks, then apply the verdict.

    The task selection happens before either condition is evaluated, so neither
    evaluator sees training or test tasks.  The mutation path is intentionally
    absent: only the independent promotion module receives the two validation
    summaries and only its decision is passed to the status writer.
    """

    if isinstance(candidate_skill, CandidateSkill):
        candidate_name = parent_skill.name
        candidate_parent = candidate_skill.parent_version
        candidate_version = candidate_skill.candidate_version
        candidate_markdown = candidate_skill.candidate_markdown
    else:
        candidate_name = candidate_skill.name
        candidate_parent = candidate_skill.parent
        candidate_version = candidate_skill.version
        candidate_markdown = candidate_skill.markdown
        if candidate_skill.name != parent_skill.name:
            raise ValueError("parent and candidate skills must have the same name")
    if candidate_parent != parent_skill.version:
        raise ValueError("candidate skill parent does not match the evaluated parent")
    resolved_skills_root = skills_root
    if resolved_skills_root is None:
        resolved_skills_root = _config_value(config, "skills_root")
    if resolved_skills_root is None:
        raise TypeError("skills_root or config.skills_root is required")

    validation_tasks = tasks_for_split(list(tasks), Split.VALIDATION)
    parent_collector = _RunCollector(store)
    parent_summary = evaluate_condition(
        tasks=validation_tasks,
        fixtures=fixtures,
        model=model,
        agent_config=agent_config,
        experiment_id=experiment_id,
        split=Split.VALIDATION,
        condition_name=Condition.SEED,
        runs_per_task=runs_per_task,
        base_seed=base_seed,
        store=parent_collector,
        skill_markdown=parent_skill.markdown,
        skill_version=parent_skill.version,
        pricing=pricing,
        config=config,
        fixture_data=fixture_data,
    )

    candidate_collector = _RunCollector(store)
    candidate_summary = evaluate_condition(
        tasks=validation_tasks,
        fixtures=fixtures,
        model=model,
        agent_config=agent_config,
        experiment_id=experiment_id,
        split=Split.VALIDATION,
        condition_name=Condition.EVOLVED_VERIFIED,
        runs_per_task=runs_per_task,
        base_seed=base_seed,
        store=candidate_collector,
        skill_markdown=candidate_markdown,
        skill_version=candidate_version,
        pricing=pricing,
        parent_runs=parent_collector.runs,
        config=config,
        fixture_data=fixture_data,
    )

    if policy is None:
        policy = _config_value(config, "promotion")
    if policy is None:
        raise TypeError("policy or config.promotion is required")

    decision = decide_promotion(parent_summary, candidate_summary, policy)
    apply_promotion(resolved_skills_root, candidate_name, decision)
    return decision


def _evaluate_slot(
    *,
    task: Task,
    fixtures: Mapping[str, Any],
    model: ChatModel,
    agent_config: AgentConfig,
    experiment_id: str,
    condition: Condition,
    skill_markdown: str | None,
    skill_version: str | None,
    split: Split,
    run_slot: int,
    seed: int,
    pricing: Mapping[str, Any] | None,
) -> RunRecord:
    capturing_model = _UsageCapturingModel(model)
    try:
        trajectory = run_agent(
            task=task,
            skill_markdown=skill_markdown,
            model=capturing_model,
            tools=ToolEnvironment(dict(fixtures)),
            config=agent_config,
            seed=seed,
            experiment_id=experiment_id,
            skill_version=skill_version,
        )
    except Exception:
        trajectory = Trajectory(
            experiment_id=experiment_id,
            task_id=task.id,
            skill_version=skill_version,
            messages=[],
            tool_calls=[],
            final_output={},
            tokens=0,
            latency_ms=0,
            outcome=Outcome.AGENT_ERROR,
        )

    try:
        verification = verify(task, trajectory)
    except Exception:
        verification = VerificationResult(
            success=False,
            checks={"verification_error": False},
            evidence={},
        )

    success = trajectory.outcome is Outcome.SUCCESS and verification.success
    input_tokens = sum(usage.input_tokens for usage in capturing_model.usages)
    output_tokens = sum(usage.output_tokens for usage in capturing_model.usages)
    estimated_cost = sum(
        estimate_cost(
            usage.input_tokens,
            usage.output_tokens,
            pricing,
            model_id=usage.model_id,
        )
        for usage in capturing_model.usages
    )
    error_text = None if success else _failure_text(trajectory, verification)
    return RunRecord(
        run_id=_run_id(experiment_id, task.id, condition, skill_version, run_slot, seed),
        experiment_id=experiment_id,
        task_id=task.id,
        split=split,
        condition_name=condition,
        skill_version=skill_version,
        run_slot=run_slot,
        seed=seed,
        outcome=trajectory.outcome,
        success=success,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=trajectory.tokens,
        latency_ms=trajectory.latency_ms,
        estimated_cost_usd=estimated_cost,
        trajectory=trajectory,
        verification=verification,
        error_text=error_text,
    )


def _indexed_tasks(tasks: Sequence[Task], split: Split) -> list[tuple[int, Task]]:
    ordered = sorted(tasks, key=lambda task: task.id)
    selected = tasks_for_split(ordered, split)
    selected_ids = {task.id for task in selected}
    return [(index, task) for index, task in enumerate(ordered) if task.id in selected_ids]


def _run_id(
    experiment_id: str,
    task_id: str,
    condition: Condition,
    skill_version: str | None,
    run_slot: int,
    seed: int,
) -> str:
    material = json.dumps(
        {
            "condition": condition.value,
            "experiment_id": experiment_id,
            "run_slot": run_slot,
            "seed": seed,
            "skill_version": skill_version,
            "task_id": task_id,
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"run-{digest}"


def _usage_from_response(response: Any, model: ChatModel) -> _Usage:
    input_tokens = _nonnegative_int(_response_value(response, "input_tokens"))
    output_tokens = _nonnegative_int(_response_value(response, "output_tokens"))
    model_id = _response_value(response, "model")
    if not isinstance(model_id, str) or not model_id:
        candidate = getattr(model, "model_id", None)
        if isinstance(candidate, str) and candidate:
            model_id = candidate
        else:
            config = getattr(model, "config", None)
            candidate = getattr(config, "model", None)
            model_id = candidate if isinstance(candidate, str) and candidate else None
    return _Usage(input_tokens, output_tokens, model_id)


def _response_value(response: Any, name: str) -> Any:
    if isinstance(response, Mapping):
        return response.get(name)
    return getattr(response, name, None)


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _failure_text(trajectory: Trajectory, verification: VerificationResult) -> str:
    if trajectory.outcome is not Outcome.SUCCESS:
        return trajectory.outcome.value
    if not verification.success:
        return "verification_failed"
    return "failed"


def _split_value(value: Split | str) -> Split:
    if isinstance(value, Split):
        return value
    return Split(str(value).replace("-", "_"))


def _condition_value(value: Condition | str) -> Condition:
    if isinstance(value, Condition):
        return value
    return Condition(str(value).replace("-", "_"))


def _resolve_skill(
    *,
    skill: Skill | str | None,
    skill_markdown: str | None,
    skill_version: str | None,
    condition: Condition,
) -> tuple[str | None, str | None]:
    if skill is not None:
        if isinstance(skill, str):
            skill_markdown = skill
        else:
            skill_markdown = getattr(skill, "markdown", getattr(skill, "skill_markdown", None))
            skill_version = getattr(skill, "version", skill_version)
    if condition is Condition.NO_SKILL:
        return None, None
    return skill_markdown, skill_version


def _resolve_config(config: Any | None) -> Any | None:
    return config


def _config_value(config: Any | None, name: str) -> Any:
    if config is None:
        return None
    if isinstance(config, Mapping):
        return config.get(name)
    return getattr(config, name, None)


def _config_model(config: Any | None) -> ChatModel | None:
    candidate = _config_value(config, "model")
    if candidate is not None and hasattr(candidate, "complete"):
        return candidate
    return None


def _load_fixtures(config: Any | None) -> Mapping[str, Any] | None:
    path = _config_value(config, "fixtures_path")
    if path is None:
        return None
    return _read_fixture_file(path)


def _read_fixture_file(path: Path | str) -> Mapping[str, Any] | None:
    with Path(path).expanduser().open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, Mapping) else None
