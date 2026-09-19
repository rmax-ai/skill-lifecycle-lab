"""Deterministic experiment evaluation for baseline and later workflows."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from skill_lab.agent import AgentConfig, ChatModel, run_agent
from skill_lab.metrics import EvaluationSummary, estimate_cost, summarize_runs
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
    Trajectory,
    VerificationResult,
)
from skill_lab.mutation import (
    CandidateResponseError,
    MutationConfig,
    TrainFailurePacket,
    build_mutation_prompt,
    propose_mutation,
    select_train_failures,
)
from skill_lab.promotion import PromotionPolicy, apply_promotion, decide_promotion
from skill_lab.skills import Skill, load_skill, write_candidate
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


class GenerationRecord(BaseModel):
    """The bounded, serializable record for one evolution generation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    generation: int = Field(ge=1)
    parent_version: str
    candidate_version: str | None = None
    mutation_status: MutationStatus
    train_failure_task_ids: list[str] = Field(default_factory=list)
    validation_parent: dict[str, Any] | None = None
    validation_candidate: dict[str, Any] | None = None
    decision: Decision | None = None
    reason_codes: list[RejectionReason] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)

    @property
    def candidate_id(self) -> str | None:
        """Return the generated candidate identifier when one exists."""

        candidate_id = self.evidence.get("candidate_id")
        return candidate_id if isinstance(candidate_id, str) else None

    @property
    def error(self) -> str | None:
        """Return the bounded error text for a generation failure."""

        error = self.evidence.get("error")
        return error if isinstance(error, str) else None

    def __getitem__(self, key: str) -> Any:
        """Allow generation records to be consumed like their JSON form."""

        if key == "candidate_id":
            return self.candidate_id
        if key == "error":
            return self.error
        return self.model_dump(mode="json")[key]

    def get(self, key: str, default: Any = None) -> Any:
        """Provide a small mapping-compatible convenience API."""

        if key in {"candidate_id", "error"}:
            value = self[key]
            return default if value is None else value
        return self.model_dump(mode="json").get(key, default)


class ExperimentResult(BaseModel):
    """Result of one bounded verified or naive evolution run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_id: str
    mode: Literal["verified", "naive"]
    generations: list[GenerationRecord]
    final_skill: Skill
    train_summaries: list[EvaluationSummary] = Field(default_factory=list)

    @property
    def current_skill(self) -> Skill:
        """Return the skill selected after the final requested generation."""

        return self.final_skill

    @property
    def final_version(self) -> str:
        """Return the selected terminal skill version."""

        return self.final_skill.version

    @property
    def generation_records(self) -> list[GenerationRecord]:
        """Compatibility alias for callers that use the explicit record name."""

        return self.generations


class _MutationCaptureModel:
    """Forward one mutation request while retaining its exact model exchange."""

    def __init__(self, model: ChatModel) -> None:
        self._model = model
        self.request: dict[str, Any] | None = None
        self.response: Any = None

    def complete(self, request: dict[str, Any]) -> Any:
        self.request = request
        self.response = self._model.complete(request)
        return self.response


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


def evolve(
    tasks: Sequence[Task],
    fixtures: Mapping[str, Any] | Path | str | None = None,
    model: ChatModel | None = None,
    agent_config: AgentConfig | None = None,
    *,
    experiment_id: str,
    mode: str | Condition = "verified",
    generations: int = 1,
    runs_per_task: int = 1,
    parent_skill: Skill | None = None,
    seed_skill: Skill | None = None,
    initial_skill: Skill | None = None,
    current_skill: Skill | None = None,
    skill: Skill | str | None = None,
    skill_name: str | None = None,
    skill_version: str = "v001",
    skills_root: Path | str | None = None,
    policy: PromotionPolicy | Mapping[str, Any] | Any | None = None,
    base_seed: int = 1729,
    store: ExperimentStore | None = None,
    pricing: Mapping[str, Any] | None = None,
    config: Any | None = None,
    mutation_config: MutationConfig | Mapping[str, Any] | None = None,
    task_inputs: Mapping[str, str] | None = None,
    fixture_data: Mapping[str, Any] | None = None,
    seed: int | None = None,
    runs: int | None = None,
    experiment_store: ExperimentStore | None = None,
) -> ExperimentResult:
    """Run exactly ``generations`` bounded mutation generations.

    Verified and naive modes deliberately share train evaluation, failure
    selection, mutation prompting, candidate writing, and candidate identity.
    Only the post-write rule differs: verified mode runs the validation gate,
    while naive mode selects the candidate immediately.
    """

    resolved_mode = _evolution_mode(mode)
    if generations < 1:
        raise ValueError("generations must be at least 1")
    if runs is not None:
        runs_per_task = runs
    if runs_per_task < 1:
        raise ValueError("runs_per_task must be at least 1")
    if seed is not None:
        base_seed = seed
    if experiment_store is not None:
        if store is not None and store is not experiment_store:
            raise ValueError("store and experiment_store must refer to the same store")
        store = experiment_store

    resolved_config = _resolve_config(config)
    resolved_root = skills_root
    if resolved_root is None:
        resolved_root = _config_value(resolved_config, "skills_root")
    if resolved_root is None:
        raise TypeError("skills_root or config.skills_root is required")
    resolved_root = Path(resolved_root).expanduser()

    resolved_model = model or _config_model(resolved_config)
    if resolved_model is None:
        raise TypeError("model or config.model is required")
    resolved_agent_config = agent_config
    if resolved_agent_config is None:
        resolved_agent_config = _config_value(resolved_config, "agent")
    if resolved_agent_config is not None and not isinstance(resolved_agent_config, AgentConfig):
        resolved_agent_config = AgentConfig.model_validate(resolved_agent_config)
    if resolved_agent_config is None:
        raise TypeError("agent_config or config.agent is required")

    if pricing is None:
        pricing = _config_value(resolved_config, "pricing")
    if seed is None:
        configured_seed = _config_value(resolved_config, "seed")
        if isinstance(configured_seed, int):
            base_seed = configured_seed

    resolved_policy = policy
    if resolved_policy is None:
        resolved_policy = _config_value(resolved_config, "promotion")
    if resolved_mode == "verified" and resolved_policy is None:
        raise TypeError("policy or config.promotion is required for verified evolution")

    resolved_mutation_config = (
        mutation_config
        if isinstance(mutation_config, MutationConfig)
        else MutationConfig.model_validate(mutation_config or {})
    )
    resolved_skill = _resolve_evolution_skill(
        parent_skill=parent_skill,
        seed_skill=seed_skill,
        initial_skill=initial_skill,
        current_skill=current_skill,
        skill=skill,
        skill_name=skill_name,
        skill_version=skill_version,
        skills_root=resolved_root,
    )
    train_tasks = tasks_for_split(list(tasks), Split.TRAIN)
    resolved_task_inputs = (
        dict(task_inputs)
        if task_inputs is not None
        else {task.id: task.input for task in train_tasks}
    )
    condition = (
        Condition.EVOLVED_VERIFIED if resolved_mode == "verified" else Condition.EVOLVED_NAIVE
    )

    if store is not None:
        _ensure_store_experiment(
            store=store,
            experiment_id=experiment_id,
            mode=resolved_mode,
            model=resolved_model,
            base_seed=base_seed,
            runs_per_task=runs_per_task,
            generations=generations,
        )
        _ensure_store_skill(store, resolved_skill)

    generation_records: list[GenerationRecord] = []
    train_summaries: list[EvaluationSummary] = []

    for generation in range(1, generations + 1):
        parent = resolved_skill
        parent_version = parent.version
        train_collector = _RunCollector(store)
        failure_packets: list[TrainFailurePacket] = []
        try:
            train_summary = evaluate_condition(
                tasks=train_tasks,
                fixtures=fixtures,
                model=resolved_model,
                agent_config=resolved_agent_config,
                experiment_id=experiment_id,
                split=Split.TRAIN,
                condition_name=condition,
                runs_per_task=runs_per_task,
                base_seed=base_seed,
                store=train_collector,
                skill_markdown=parent.markdown,
                skill_version=parent.version,
                pricing=pricing,
                config=resolved_config,
                fixture_data=fixture_data,
            )
            train_summaries.append(train_summary)
            failure_packets = select_train_failures(
                train_collector.runs,
                task_inputs=resolved_task_inputs,
            )
        except Exception as error:
            generation_records.append(
                _generation_error(
                    generation=generation,
                    parent_version=parent_version,
                    train_failure_task_ids=[],
                    error=error,
                )
            )
            continue

        failure_ids = [packet.task_id for packet in failure_packets]
        prompt: dict[str, Any] | None = None
        capture = _MutationCaptureModel(resolved_model)
        candidate: CandidateSkill | None = None
        candidate_skill: Skill | None = None
        try:
            prompt = build_mutation_prompt(parent, failure_packets, resolved_mutation_config)
            candidate = propose_mutation(
                parent,
                failure_packets,
                capture,
                resolved_mutation_config,
            )
            status = (
                MutationStatus.PROPOSED.value
                if resolved_mode == "verified"
                else MutationStatus.NAIVE_REPLACED.value
            )
            write_candidate(
                resolved_root,
                candidate=candidate,
                parent_skill=parent,
                status=status,
            )
            candidate_skill = load_skill(
                resolved_root,
                parent.name,
                candidate.candidate_version,
            )
        except Exception as error:
            if candidate is not None and store is not None:
                _persist_candidate(
                    store=store,
                    experiment_id=experiment_id,
                    candidate=candidate,
                    candidate_skill=candidate_skill,
                    prompt=prompt or capture.request or {},
                    response=capture.response,
                    status=candidate.status,
                )
            generation_records.append(
                _generation_error(
                    generation=generation,
                    parent_version=parent_version,
                    candidate_version=(
                        candidate.candidate_version if candidate is not None else None
                    ),
                    candidate_id=candidate.candidate_id if candidate is not None else None,
                    train_failure_task_ids=failure_ids,
                    error=error,
                    reason=(
                        RejectionReason.CANDIDATE_INVALID
                        if isinstance(error, CandidateResponseError)
                        else RejectionReason.EVALUATION_ERROR
                    ),
                )
            )
            continue

        if resolved_mode == "naive":
            decision = PromotionDecision(
                candidate_id=candidate.candidate_id,
                parent_version=parent.version,
                candidate_version=candidate.candidate_version,
                decision=Decision.NAIVE_REPLACE,
            )
            resolved_skill = candidate_skill
            if store is not None:
                _persist_candidate(
                    store=store,
                    experiment_id=experiment_id,
                    candidate=candidate,
                    candidate_skill=candidate_skill,
                    prompt=prompt or capture.request or {},
                    response=capture.response,
                    status=MutationStatus.NAIVE_REPLACED,
                    decision=decision,
                    generation=generation,
                )
            generation_records.append(
                _generation_record(
                    generation=generation,
                    parent_version=parent_version,
                    candidate=candidate,
                    mutation_status=MutationStatus.NAIVE_REPLACED,
                    decision=decision,
                    train_failure_task_ids=failure_ids,
                )
            )
            continue

        try:
            decision = evaluate_validation_gate(
                tasks=tasks,
                fixtures=fixtures,
                model=resolved_model,
                agent_config=resolved_agent_config,
                experiment_id=experiment_id,
                parent_skill=parent,
                candidate_skill=candidate,
                skills_root=resolved_root,
                policy=resolved_policy,
                runs_per_task=runs_per_task,
                base_seed=base_seed,
                store=store,
                pricing=pricing,
                config=resolved_config,
                fixture_data=fixture_data,
            )
            if decision.decision is Decision.PROMOTE:
                resolved_skill = load_skill(
                    resolved_root,
                    parent.name,
                    candidate.candidate_version,
                )
                mutation_status = MutationStatus.PROMOTED
            elif decision.decision is Decision.REJECT:
                mutation_status = MutationStatus.REJECTED
            else:
                raise ValueError("verified validation returned a non-selection decision")
            if store is not None:
                _persist_candidate(
                    store=store,
                    experiment_id=experiment_id,
                    candidate=candidate,
                    candidate_skill=load_skill(
                        resolved_root,
                        parent.name,
                        candidate.candidate_version,
                    ),
                    prompt=prompt or capture.request or {},
                    response=capture.response,
                    status=mutation_status,
                    decision=decision,
                    generation=generation,
                )
            generation_records.append(
                _generation_record(
                    generation=generation,
                    parent_version=parent_version,
                    candidate=candidate,
                    mutation_status=mutation_status,
                    decision=decision,
                    train_failure_task_ids=failure_ids,
                )
            )
        except Exception as error:
            if store is not None:
                _persist_candidate(
                    store=store,
                    experiment_id=experiment_id,
                    candidate=candidate,
                    candidate_skill=candidate_skill,
                    prompt=prompt or capture.request or {},
                    response=capture.response,
                    status=MutationStatus.PROPOSED,
                )
            generation_records.append(
                _generation_error(
                    generation=generation,
                    parent_version=parent_version,
                    candidate_version=candidate.candidate_version,
                    candidate_id=candidate.candidate_id,
                    train_failure_task_ids=failure_ids,
                    error=error,
                    reason=RejectionReason.EVALUATION_ERROR,
                )
            )

    return ExperimentResult(
        experiment_id=experiment_id,
        mode=resolved_mode,
        generations=generation_records,
        final_skill=resolved_skill,
        train_summaries=train_summaries,
    )


def _evolution_mode(mode: str | Condition) -> Literal["verified", "naive"]:
    value = mode.value if isinstance(mode, Condition) else str(mode).replace("-", "_")
    if value in {"verified", Condition.EVOLVED_VERIFIED.value}:
        return "verified"
    if value in {"naive", Condition.EVOLVED_NAIVE.value}:
        return "naive"
    raise ValueError("mode must be 'verified' or 'naive'")


def _resolve_evolution_skill(
    *,
    parent_skill: Skill | None,
    seed_skill: Skill | None,
    initial_skill: Skill | None,
    current_skill: Skill | None,
    skill: Skill | str | None,
    skill_name: str | None,
    skill_version: str,
    skills_root: Path,
) -> Skill:
    supplied = [
        value
        for value in (parent_skill, seed_skill, initial_skill, current_skill)
        if value is not None
    ]
    if supplied:
        resolved = supplied[0]
        if any(value != resolved for value in supplied[1:]):
            raise ValueError("evolution skill aliases must refer to the same skill")
        return resolved
    if isinstance(skill, Skill):
        return skill
    resolved_name = skill if isinstance(skill, str) else skill_name
    if resolved_name is None:
        resolved_name = "incident-response"
    return load_skill(skills_root, resolved_name, skill_version)


def _ensure_store_experiment(
    *,
    store: ExperimentStore,
    experiment_id: str,
    mode: str,
    model: ChatModel,
    base_seed: int,
    runs_per_task: int,
    generations: int,
) -> None:
    row = store.connection.execute(
        "SELECT experiment_id FROM experiments WHERE experiment_id = ?",
        (experiment_id,),
    ).fetchone()
    if row is not None:
        return
    model_id = getattr(model, "model_id", None)
    if not isinstance(model_id, str) or not model_id:
        model_id = "unknown-model"
    store.insert_experiment(
        experiment_id=experiment_id,
        created_at="2000-01-01T00:00:00.000Z",
        mode=mode,
        config_json={
            "generations": generations,
            "mode": mode,
            "runs_per_task": runs_per_task,
        },
        git_commit="unavailable",
        dataset_sha256="unavailable",
        model_id=model_id,
        seed=base_seed,
        runs_per_task=runs_per_task,
        status="running",
    )


def _ensure_store_skill(store: ExperimentStore, skill: Skill) -> None:
    row = store.connection.execute(
        "SELECT version FROM skills WHERE version = ?",
        (skill.version,),
    ).fetchone()
    if row is not None:
        return
    store.insert_skill(
        version=skill.version,
        name=skill.name,
        parent_version=skill.parent,
        generation=skill.generation,
        created_by=skill.created_by,
        status=skill.status,
        skill_markdown=skill.markdown,
        metadata_json=skill.metadata.model_dump(mode="json"),
        created_at="2000-01-01T00:00:00.000Z",
    )


def _persist_candidate(
    *,
    store: ExperimentStore,
    experiment_id: str,
    candidate: CandidateSkill,
    candidate_skill: Skill | None,
    prompt: Mapping[str, Any],
    response: Any,
    status: MutationStatus,
    decision: PromotionDecision | None = None,
    generation: int | None = None,
) -> None:
    if candidate_skill is None:
        return
    persisted_candidate = candidate.model_copy(update={"status": status})
    row = store.connection.execute(
        "SELECT candidate_id FROM mutations WHERE candidate_id = ?",
        (candidate.candidate_id,),
    ).fetchone()
    if row is None:
        store.insert_mutation(
            experiment_id=experiment_id,
            candidate=persisted_candidate,
            prompt_json=prompt,
            response_json=response,
        )

    skill_row = store.connection.execute(
        "SELECT version FROM skills WHERE version = ?",
        (candidate_skill.version,),
    ).fetchone()
    if skill_row is None:
        store.insert_skill(
            version=candidate_skill.version,
            name=candidate_skill.name,
            parent_version=candidate_skill.parent,
            generation=candidate_skill.generation,
            created_by=candidate_skill.created_by,
            status=candidate_skill.status,
            skill_markdown=candidate_skill.markdown,
            metadata_json=candidate_skill.metadata.model_dump(mode="json"),
            created_at=f"2000-01-01T00:00:{candidate_skill.generation:02d}.000Z",
        )

    if decision is not None:
        stored_decision = decision.model_copy(update={"candidate_id": candidate.candidate_id})
        decision_id = _decision_id(experiment_id, stored_decision)
        decision_row = store.connection.execute(
            "SELECT decision_id FROM promotion_decisions WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()
        if decision_row is None:
            store.insert_promotion_decision(
                experiment_id=experiment_id,
                decision=stored_decision,
                decision_id=decision_id,
                created_at=f"2000-01-01T00:00:{(generation or 0):02d}.000Z",
            )


def _decision_id(experiment_id: str, decision: PromotionDecision) -> str:
    material = json.dumps(
        {
            "candidate_id": decision.candidate_id,
            "candidate_version": decision.candidate_version,
            "decision": decision.decision.value,
            "experiment_id": experiment_id,
        },
        sort_keys=True,
    )
    return f"decision-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:16]}"


def _generation_record(
    *,
    generation: int,
    parent_version: str,
    candidate: CandidateSkill,
    mutation_status: MutationStatus,
    decision: PromotionDecision,
    train_failure_task_ids: Sequence[str],
) -> GenerationRecord:
    evidence = dict(decision.evidence)
    evidence["candidate_id"] = candidate.candidate_id
    return GenerationRecord(
        generation=generation,
        parent_version=parent_version,
        candidate_version=candidate.candidate_version,
        mutation_status=mutation_status,
        train_failure_task_ids=list(train_failure_task_ids),
        validation_parent=_mapping_value(evidence.get("parent")),
        validation_candidate=_mapping_value(evidence.get("candidate")),
        decision=decision.decision,
        reason_codes=list(decision.reason_codes),
        evidence=evidence,
    )


def _generation_error(
    *,
    generation: int,
    parent_version: str,
    error: Exception,
    train_failure_task_ids: Sequence[str],
    candidate_version: str | None = None,
    candidate_id: str | None = None,
    reason: RejectionReason = RejectionReason.EVALUATION_ERROR,
) -> GenerationRecord:
    evidence: dict[str, Any] = {
        "error": f"{type(error).__name__}: {error}",
        "error_type": type(error).__name__,
    }
    if candidate_id is not None:
        evidence["candidate_id"] = candidate_id
    return GenerationRecord(
        generation=generation,
        parent_version=parent_version,
        candidate_version=candidate_version,
        mutation_status=MutationStatus.GENERATION_ERROR,
        train_failure_task_ids=list(train_failure_task_ids),
        reason_codes=[reason],
        evidence=evidence,
    )


def _mapping_value(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, Mapping) else None


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
