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
