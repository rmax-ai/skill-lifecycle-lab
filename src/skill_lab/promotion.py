"""Deterministic validation-evidence promotion gate."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from skill_lab.metrics import EvaluationSummary
from skill_lab.models import Decision, PromotionDecision, RejectionReason


class PromotionPolicy(BaseModel):
    """Thresholds used by the independent promotion decision."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    regression_threshold: float = Field(ge=0, le=1)
    cost_tolerance: float = Field(ge=0)


def decide_promotion(
    parent: EvaluationSummary,
    candidate: EvaluationSummary,
    policy: PromotionPolicy,
) -> PromotionDecision:
    """Decide promotion using only the two validation summaries and policy."""

    policy_model = _policy_model(policy)
    inconclusive = _inconclusive(parent, candidate, policy_model)
    evidence = _evidence(parent, candidate, policy_model)
    reasons: list[RejectionReason]

    if inconclusive:
        reasons = [RejectionReason.INCONCLUSIVE]
    else:
        reasons = []
        if candidate.success_rate <= parent.success_rate:
            reasons.append(RejectionReason.NO_VALIDATION_LIFT)
        if candidate.regression_rate > policy_model.regression_threshold:
            reasons.append(RejectionReason.REGRESSION_THRESHOLD_EXCEEDED)
        if candidate.prohibited_actions > parent.prohibited_actions:
            reasons.append(RejectionReason.PROHIBITED_ACTIONS_INCREASED)
        if candidate.estimated_cost_usd > parent.estimated_cost_usd * policy_model.cost_tolerance:
            reasons.append(RejectionReason.COST_TOLERANCE_EXCEEDED)

    evidence["reason_codes"] = [reason.value for reason in reasons]
    return PromotionDecision(
        candidate_id=_candidate_id(candidate),
        parent_version=_version(parent),
        candidate_version=_version(candidate),
        decision=Decision.REJECT if reasons else Decision.PROMOTE,
        reason_codes=reasons,
        evidence=evidence,
    )


def apply_promotion(
    root: Path | str,
    skill_name: str,
    decision: PromotionDecision,
) -> Path:
    """Apply a harness decision to candidate metadata and return its path.

    This is intentionally the only status writer in the promotion module.
    A rejected decision marks the candidate rejected; a promoted decision
    marks it promoted. The function does not infer or manufacture a decision.
    """

    if decision.decision not in {Decision.PROMOTE, Decision.REJECT}:
        raise ValueError("only promote or reject decisions can update metadata")
    candidate_dir = Path(root).expanduser() / skill_name / decision.candidate_version
    metadata_path = candidate_dir / "metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("candidate metadata is not readable") from error
    if not isinstance(metadata, dict):
        raise ValueError("candidate metadata must be an object")
    if metadata.get("parent") != decision.parent_version:
        raise ValueError("candidate metadata parent does not match decision")
    metadata["status"] = "promoted" if decision.decision is Decision.PROMOTE else "rejected"
    metadata_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return candidate_dir


def _policy_model(policy: PromotionPolicy | Mapping[str, Any] | Any) -> PromotionPolicy:
    if isinstance(policy, PromotionPolicy):
        return policy
    if isinstance(policy, Mapping):
        payload = dict(policy)
    else:
        try:
            payload = {
                "regression_threshold": policy.regression_threshold,
                "cost_tolerance": policy.cost_tolerance,
            }
        except AttributeError as error:
            raise TypeError("policy must expose promotion thresholds") from error
    try:
        return PromotionPolicy.model_validate(payload)
    except ValueError as error:
        raise TypeError("policy must expose valid promotion thresholds") from error


def _inconclusive(
    parent: EvaluationSummary,
    candidate: EvaluationSummary,
    policy: PromotionPolicy,
) -> bool:
    if _has_nonfinite(parent) or _has_nonfinite(candidate):
        return True
    if not math.isfinite(policy.regression_threshold) or not math.isfinite(policy.cost_tolerance):
        return True
    if parent.split is not None and parent.split.value != "validation":
        return True
    if candidate.split is not None and candidate.split.value != "validation":
        return True
    if parent.run_count <= 0 or candidate.run_count <= 0:
        return True
    if candidate.regression_denominator <= 0 or candidate.regression_rate is None:
        return True
    return _has_error_outcome(parent) or _has_error_outcome(candidate)


def _has_error_outcome(summary: EvaluationSummary) -> bool:
    return any(
        count > 0 and (name.endswith("_error") or name == "budget_exhausted")
        for name, count in summary.outcome_counts.items()
    )


def _evidence(
    parent: EvaluationSummary,
    candidate: EvaluationSummary,
    policy: PromotionPolicy,
) -> dict[str, Any]:
    parent_data = _json_safe(parent.model_dump(mode="json"))
    candidate_data = _json_safe(candidate.model_dump(mode="json"))
    skill_lift = (
        candidate.success_rate - parent.success_rate
        if math.isfinite(candidate.success_rate) and math.isfinite(parent.success_rate)
        else None
    )
    cost_limit = (
        parent.estimated_cost_usd * policy.cost_tolerance
        if math.isfinite(parent.estimated_cost_usd) and math.isfinite(policy.cost_tolerance)
        else None
    )
    return _json_safe(
        {
            "parent": parent_data,
            "candidate": candidate_data,
            "policy": policy.model_dump(mode="json"),
            "parent_success_rate": parent.success_rate,
            "candidate_success_rate": candidate.success_rate,
            "skill_lift": skill_lift,
            "regression_rate": candidate.regression_rate,
            "regression_threshold": policy.regression_threshold,
            "parent_run_count": parent.run_count,
            "candidate_run_count": candidate.run_count,
            "parent_prohibited_actions": parent.prohibited_actions,
            "candidate_prohibited_actions": candidate.prohibited_actions,
            "parent_cost_usd": parent.estimated_cost_usd,
            "candidate_cost_usd": candidate.estimated_cost_usd,
            "cost_limit_usd": cost_limit,
            "rates": {
                "parent_success_rate": parent.success_rate,
                "candidate_success_rate": candidate.success_rate,
                "candidate_regression_rate": candidate.regression_rate,
                "skill_lift": skill_lift,
                "validation_lift": skill_lift,
            },
            "counts": {
                "parent_run_count": parent.run_count,
                "candidate_run_count": candidate.run_count,
                "parent_successful_runs": parent.successful_runs,
                "candidate_successful_runs": candidate.successful_runs,
                "parent_prohibited_actions": parent.prohibited_actions,
                "candidate_prohibited_actions": candidate.prohibited_actions,
                "candidate_regression_denominator": candidate.regression_denominator,
            },
            "costs": {
                "parent_cost_usd": parent.estimated_cost_usd,
                "candidate_cost_usd": candidate.estimated_cost_usd,
                "cost_limit_usd": cost_limit,
            },
            "reason_codes": [],
        }
    )


def _has_nonfinite(summary: EvaluationSummary) -> bool:
    return _contains_nonfinite(summary.model_dump(mode="python"))


def _contains_nonfinite(value: Any) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, Mapping):
        return any(_contains_nonfinite(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_nonfinite(item) for item in value)
    return False


def _json_safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def _candidate_id(summary: EvaluationSummary) -> str:
    source = f"{summary.experiment_id or ''}:{summary.skill_version or 'v000'}"
    return f"cand-{hashlib.sha256(source.encode('utf-8')).hexdigest()[:12]}"


def _version(summary: EvaluationSummary) -> str:
    return summary.skill_version or "v000"
