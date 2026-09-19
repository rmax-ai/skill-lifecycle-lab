"""Deterministic outcome, trajectory, and economic metrics."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from statistics import pvariance
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from skill_lab.models import Condition, Outcome, RunRecord, Split, Version


class EvaluationSummary(BaseModel):
    """Aggregated evidence for one condition and split."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_id: str | None = None
    split: Split | None = None
    condition_name: Condition | None = Field(
        default=None,
        validation_alias=AliasChoices("condition_name", "condition"),
    )
    skill_version: Version | None = None
    task_count: int = Field(default=0, ge=0)
    run_count: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("run_count", "total_runs", "runs", "denominator"),
    )
    successful_runs: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("successful_runs", "success_count", "successes"),
    )
    success_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    success_variance: float = Field(
        default=0.0,
        ge=0.0,
        validation_alias=AliasChoices("success_variance", "variance"),
    )
    regression_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    regression_denominator: int = Field(default=0, ge=0)
    previously_unsolved_solved: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices(
            "previously_unsolved_solved",
            "solved_relative_parent",
            "solved",
        ),
    )
    previously_solved_broken: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices(
            "previously_solved_broken",
            "broken_relative_parent",
            "broken",
        ),
    )
    parent_run_count: int = Field(default=0, ge=0)
    parent_successes: int = Field(default=0, ge=0)
    parent_success_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    skill_lift: float | None = None
    no_skill_success_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    improvement_over_no_skill: float | None = None
    tool_calls: int = Field(default=0, ge=0)
    invalid_tool_calls: int = Field(default=0, ge=0)
    prohibited_actions: int = Field(default=0, ge=0)
    unnecessary_actions: int = Field(default=0, ge=0)
    steps: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    latency_ms: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("latency_ms", "total_latency_ms"),
    )
    estimated_cost_usd: float = Field(
        default=0.0,
        ge=0.0,
        validation_alias=AliasChoices("estimated_cost_usd", "total_cost_usd"),
    )
    failed_runs: int = Field(default=0, ge=0)
    outcome_counts: dict[str, int] = Field(default_factory=dict)
    task_success_means: dict[str, float] = Field(default_factory=dict)

    @property
    def condition(self) -> Condition | None:
        """Compatibility alias for the condition vocabulary."""

        return self.condition_name

    @property
    def runs(self) -> int:
        """Return the number of completed run slots."""

        return self.run_count

    @property
    def successes(self) -> int:
        """Return the number of successful run slots."""

        return self.successful_runs

    @property
    def success_count(self) -> int:
        """Compatibility alias for the successful slot count."""

        return self.successful_runs

    @property
    def failure_count(self) -> int:
        """Return the number of unsuccessful slots."""

        return self.failed_runs

    @property
    def total_runs(self) -> int:
        """Compatibility alias for the completed slot count."""

        return self.run_count

    @property
    def denominator(self) -> int:
        """Return the denominator used by the success rate."""

        return self.run_count

    @property
    def variance(self) -> float:
        """Compatibility alias for population variance."""

        return self.success_variance

    @property
    def task_success_rate(self) -> float:
        """Compatibility alias for the aggregated success rate."""

        return self.success_rate

    @property
    def no_skill_lift(self) -> float | None:
        """Return improvement over the no-skill baseline when supplied."""

        return self.improvement_over_no_skill

    @property
    def solved_relative_parent(self) -> int:
        """Return parent failures that became successful slots."""

        return self.previously_unsolved_solved

    @property
    def broken_relative_parent(self) -> int:
        """Return parent successes that became failed slots."""

        return self.previously_solved_broken

    @property
    def solved(self) -> int:
        """Compatibility alias for newly solved slots."""

        return self.previously_unsolved_solved

    @property
    def broken(self) -> int:
        """Compatibility alias for broken parent slots."""

        return self.previously_solved_broken

    @property
    def action_counts(self) -> dict[str, int]:
        """Return all trajectory action counts in one stable mapping."""

        return {
            "tool_calls": self.tool_calls,
            "invalid_tool_calls": self.invalid_tool_calls,
            "prohibited_actions": self.prohibited_actions,
            "unnecessary_actions": self.unnecessary_actions,
            "steps": self.steps,
        }

    @property
    def total_latency_ms(self) -> int:
        """Return total model-reported latency."""

        return self.latency_ms

    @property
    def total_cost_usd(self) -> float:
        """Return total estimated provider cost."""

        return self.estimated_cost_usd


def summarize_runs(
    runs: list[RunRecord],
    pricing: Mapping[str, Any] | None = None,
    parent_runs: Sequence[RunRecord] | None = None,
    no_skill_runs: Sequence[RunRecord] | None = None,
) -> EvaluationSummary:
    """Aggregate completed run slots without dropping failures.

    Variance is population variance over the per-task success means. Relative
    parent metrics compare matching ``task_id`` and ``run_slot`` records.
    """

    ordered_runs = sorted(runs, key=_run_sort_key)
    parent_records = list(parent_runs or ())
    no_skill_records = list(no_skill_runs or ())

    task_means = _task_success_means(ordered_runs)
    successful_runs = sum(run.success for run in ordered_runs)
    run_count = len(ordered_runs)
    parent_metrics = _relative_metrics(ordered_runs, parent_records)
    no_skill_rate = _success_rate(no_skill_records)
    model_id = _model_id_for_pricing(pricing)

    cost = sum(_run_cost(run, pricing, model_id=model_id) for run in ordered_runs)
    parent_success_rate = parent_metrics["parent_success_rate"]
    skill_lift = (
        None if parent_success_rate is None else _success_rate(ordered_runs) - parent_success_rate
    )
    no_skill_lift = None if not no_skill_records else _success_rate(ordered_runs) - no_skill_rate
    if no_skill_records:
        no_skill_rate = _success_rate(no_skill_records)

    summary = EvaluationSummary(
        experiment_id=ordered_runs[0].experiment_id if ordered_runs else None,
        split=ordered_runs[0].split if ordered_runs else None,
        condition_name=ordered_runs[0].condition_name if ordered_runs else None,
        skill_version=ordered_runs[0].skill_version if ordered_runs else None,
        task_count=len(task_means),
        run_count=run_count,
        successful_runs=successful_runs,
        success_rate=_success_rate(ordered_runs),
        success_variance=pvariance(tuple(task_means.values())) if task_means else 0.0,
        regression_rate=parent_metrics["regression_rate"],
        regression_denominator=parent_metrics["regression_denominator"],
        previously_unsolved_solved=parent_metrics["solved"],
        previously_solved_broken=parent_metrics["broken"],
        parent_run_count=parent_metrics["parent_run_count"],
        parent_successes=parent_metrics["parent_successes"],
        parent_success_rate=parent_success_rate,
        skill_lift=skill_lift,
        no_skill_success_rate=no_skill_rate if no_skill_records else None,
        improvement_over_no_skill=no_skill_lift,
        tool_calls=sum(len(run.trajectory.tool_calls) for run in ordered_runs),
        invalid_tool_calls=sum(_invalid_tool_calls(run) for run in ordered_runs),
        prohibited_actions=sum(_evidence_count(run, "forbidden_actions") for run in ordered_runs),
        unnecessary_actions=sum(
            _evidence_count(run, "unnecessary_actions") for run in ordered_runs
        ),
        steps=sum(_steps(run) for run in ordered_runs),
        input_tokens=sum(run.input_tokens for run in ordered_runs),
        output_tokens=sum(run.output_tokens for run in ordered_runs),
        total_tokens=sum(run.total_tokens for run in ordered_runs),
        latency_ms=sum(run.latency_ms for run in ordered_runs),
        estimated_cost_usd=cost,
        failed_runs=run_count - successful_runs,
        outcome_counts=_outcome_counts(ordered_runs),
        task_success_means=task_means,
    )
    return summary


def estimate_cost(
    input_tokens: int,
    output_tokens: int,
    pricing: Mapping[str, Any] | None,
    *,
    model_id: str | None = None,
) -> float:
    """Estimate cost using configured per-million-token prices."""

    input_price, output_price = _pricing_rates(pricing, model_id=model_id)
    return input_tokens / 1_000_000 * input_price + output_tokens / 1_000_000 * output_price


def _run_sort_key(run: RunRecord) -> tuple[str, int, str]:
    return run.task_id, run.run_slot, run.run_id


def _task_success_means(runs: Sequence[RunRecord]) -> dict[str, float]:
    successes: dict[str, int] = defaultdict(int)
    totals: dict[str, int] = defaultdict(int)
    for run in runs:
        totals[run.task_id] += 1
        successes[run.task_id] += int(run.success)
    return {task_id: successes[task_id] / totals[task_id] for task_id in sorted(totals)}


def _success_rate(runs: Sequence[RunRecord]) -> float:
    if not runs:
        return 0.0
    return sum(run.success for run in runs) / len(runs)


def _relative_metrics(
    runs: Sequence[RunRecord],
    parent_runs: Sequence[RunRecord],
) -> dict[str, int | float | None]:
    parent_by_slot = {_comparison_key(run): run for run in parent_runs}
    matching = [
        (run, parent_by_slot[_comparison_key(run)])
        for run in runs
        if _comparison_key(run) in parent_by_slot
    ]
    parent_successes = sum(parent.success for _, parent in matching)
    broken = sum(parent.success and not run.success for run, parent in matching)
    solved = sum(not parent.success and run.success for run, parent in matching)
    return {
        "parent_run_count": len(matching),
        "parent_successes": parent_successes,
        "parent_success_rate": (parent_successes / len(matching) if matching else None),
        "regression_denominator": parent_successes,
        "regression_rate": broken / parent_successes if parent_successes else None,
        "solved": solved,
        "broken": broken,
    }


def _comparison_key(run: RunRecord) -> tuple[str, int]:
    return run.task_id, run.run_slot


def _invalid_tool_calls(run: RunRecord) -> int:
    return sum("error" in call.result for call in run.trajectory.tool_calls)


def _evidence_count(run: RunRecord, key: str) -> int:
    value = run.verification.evidence.get(key, [])
    return len(value) if isinstance(value, list) else 0


def _steps(run: RunRecord) -> int:
    return sum(
        isinstance(message, Mapping) and message.get("role") == "assistant"
        for message in run.trajectory.messages
    )


def _outcome_counts(runs: Sequence[RunRecord]) -> dict[str, int]:
    counts: dict[str, int] = {outcome.value: 0 for outcome in Outcome}
    for run in runs:
        outcome = run.outcome.value if isinstance(run.outcome, Outcome) else str(run.outcome)
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def _model_id_for_pricing(pricing: Mapping[str, Any] | None) -> str | None:
    if not isinstance(pricing, Mapping):
        return None
    if _looks_like_rates(pricing):
        return None
    if "mock-incident-v1" in pricing:
        return "mock-incident-v1"
    if "default" in pricing:
        return "default"
    keys = list(pricing)
    return keys[0] if len(keys) == 1 else None


def _run_cost(
    run: RunRecord,
    pricing: Mapping[str, Any] | None,
    *,
    model_id: str | None,
) -> float:
    if not pricing:
        return run.estimated_cost_usd
    if _looks_like_rates(pricing) or model_id is not None:
        return estimate_cost(
            run.input_tokens,
            run.output_tokens,
            pricing,
            model_id=model_id,
        )
    return run.estimated_cost_usd


def _pricing_rates(
    pricing: Mapping[str, Any] | None,
    *,
    model_id: str | None,
) -> tuple[float, float]:
    if not isinstance(pricing, Mapping) or not pricing:
        return 0.0, 0.0

    selected: Any = pricing
    if not _looks_like_rates(pricing):
        if model_id is not None and model_id in pricing:
            selected = pricing[model_id]
        elif "default" in pricing:
            selected = pricing["default"]
        elif "mock-incident-v1" in pricing:
            selected = pricing["mock-incident-v1"]
        elif len(pricing) == 1:
            selected = next(iter(pricing.values()))
        else:
            return 0.0, 0.0

    selected_mapping = _as_mapping(selected)
    if selected_mapping is None:
        return 0.0, 0.0
    input_price = selected_mapping.get("input_per_million_usd", 0.0)
    output_price = selected_mapping.get("output_per_million_usd", 0.0)
    return _nonnegative_float(input_price), _nonnegative_float(output_price)


def _looks_like_rates(value: Mapping[str, Any]) -> bool:
    return "input_per_million_usd" in value or "output_per_million_usd" in value


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        return dumped if isinstance(dumped, Mapping) else None
    return None


def _nonnegative_float(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return 0.0
    return converted if converted >= 0 else 0.0
