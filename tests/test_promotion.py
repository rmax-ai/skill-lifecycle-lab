import json
import math

import pytest
from pydantic import ValidationError

from skill_lab.metrics import EvaluationSummary
from skill_lab.models import Decision, RejectionReason, Split
from skill_lab.promotion import PromotionPolicy, decide_promotion

POLICY = PromotionPolicy(regression_threshold=0.10, cost_tolerance=1.10)


def summary(
    *,
    rate: float,
    successes: int,
    runs: int = 10,
    regression: float | None = 0.0,
    prohibited: int = 0,
    cost: float = 1.0,
    version: str = "v001",
    failed: int = 0,
) -> EvaluationSummary:
    return EvaluationSummary(
        split=Split.VALIDATION,
        skill_version=version,
        run_count=runs,
        successful_runs=successes,
        success_rate=rate,
        regression_rate=regression,
        regression_denominator=successes,
        prohibited_actions=prohibited,
        estimated_cost_usd=cost,
        failed_runs=failed,
        outcome_counts={"success": successes, "failure": runs - successes},
    )


def test_promotes_only_when_all_rules_pass() -> None:
    assert PromotionPolicy.model_validate_json(POLICY.model_dump_json()) == POLICY

    decision = decide_promotion(
        summary(rate=0.8, successes=8), summary(rate=0.9, successes=9, version="v002"), POLICY
    )

    assert decision.decision is Decision.PROMOTE
    assert decision.reason_codes == []
    assert decision.evidence["policy"] == POLICY.model_dump(mode="json")
    assert decision.evidence["candidate"]["successful_runs"] == 9


def test_rejects_no_lift() -> None:
    decision = decide_promotion(
        summary(rate=0.8, successes=8), summary(rate=0.8, successes=8, version="v002"), POLICY
    )

    assert decision.decision is Decision.REJECT
    assert RejectionReason.NO_VALIDATION_LIFT in decision.reason_codes


def test_rejects_regression() -> None:
    decision = decide_promotion(
        summary(rate=0.8, successes=8),
        summary(rate=0.9, successes=9, regression=0.2, version="v002"),
        POLICY,
    )

    assert decision.decision is Decision.REJECT
    assert RejectionReason.REGRESSION_THRESHOLD_EXCEEDED in decision.reason_codes


def test_rejects_prohibited_increase() -> None:
    decision = decide_promotion(
        summary(rate=0.8, successes=8),
        summary(rate=0.9, successes=9, prohibited=1, version="v002"),
        POLICY,
    )

    assert decision.decision is Decision.REJECT
    assert RejectionReason.PROHIBITED_ACTIONS_INCREASED in decision.reason_codes


def test_rejects_cost_excess() -> None:
    decision = decide_promotion(
        summary(rate=0.8, successes=8),
        summary(rate=0.9, successes=9, cost=1.2, version="v002"),
        POLICY,
    )

    assert decision.decision is Decision.REJECT
    assert RejectionReason.COST_TOLERANCE_EXCEEDED in decision.reason_codes


def test_rejects_inconclusive() -> None:
    decision = decide_promotion(
        summary(rate=0.8, successes=8),
        summary(rate=0.9, successes=9, runs=0, version="v002"),
        POLICY,
    )

    assert decision.decision is Decision.REJECT
    assert decision.reason_codes == [RejectionReason.INCONCLUSIVE]


def test_nonfinite_metrics_rejected_at_construction() -> None:
    metric_fields = (
        "success_rate",
        "regression_rate",
        "skill_lift",
        "no_skill_success_rate",
        "improvement_over_no_skill",
        "estimated_cost_usd",
    )
    for field in metric_fields:
        for value in (math.nan, math.inf, -math.inf):
            with pytest.raises(ValidationError):
                EvaluationSummary(**{field: value})

    for field in ("regression_threshold", "cost_tolerance"):
        for value in (math.nan, math.inf, -math.inf):
            with pytest.raises(ValidationError):
                PromotionPolicy(
                    regression_threshold=value if field == "regression_threshold" else 0.1,
                    cost_tolerance=value if field == "cost_tolerance" else 1.1,
                )


def test_nonfinite_constructed_metrics_are_inconclusive() -> None:
    parent = summary(rate=0.8, successes=8)
    candidate = summary(rate=0.9, successes=9, version="v002")
    for metric_field, policy_field in (
        ("estimated_cost_usd", None),
        (None, "regression_threshold"),
        (None, "cost_tolerance"),
    ):
        candidate_payload = candidate.model_dump()
        if metric_field is not None:
            candidate_payload[metric_field] = math.inf
        constructed_candidate = EvaluationSummary.model_construct(**candidate_payload)
        policy_payload = {"regression_threshold": 0.1, "cost_tolerance": 1.1}
        if policy_field is not None:
            policy_payload[policy_field] = math.inf
        policy = PromotionPolicy.model_construct(**policy_payload)

        decision = decide_promotion(parent, constructed_candidate, policy)

        assert decision.decision is Decision.REJECT
        assert decision.reason_codes == [RejectionReason.INCONCLUSIVE]
        json.dumps(decision.evidence, allow_nan=False)
