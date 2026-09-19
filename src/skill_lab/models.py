from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class Split(StrEnum):
    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"


class Condition(StrEnum):
    NO_SKILL = "no_skill"
    SEED = "seed"
    EVOLVED_VERIFIED = "evolved_verified"
    EVOLVED_NAIVE = "evolved_naive"


class Outcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    AGENT_ERROR = "agent_error"
    MODEL_ERROR = "model_error"
    TOOL_ERROR = "tool_error"
    BUDGET_EXHAUSTED = "budget_exhausted"


class Decision(StrEnum):
    PROMOTE = "promote"
    REJECT = "reject"
    NAIVE_REPLACE = "naive_replace"


class MutationStatus(StrEnum):
    PROPOSED = "proposed"
    EVALUATED = "evaluated"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    NAIVE_REPLACED = "naive_replaced"
    GENERATION_ERROR = "generation_error"


class RejectionReason(StrEnum):
    NO_VALIDATION_LIFT = "no_validation_lift"
    REGRESSION_THRESHOLD_EXCEEDED = "regression_threshold_exceeded"
    PROHIBITED_ACTIONS_INCREASED = "prohibited_actions_increased"
    COST_TOLERANCE_EXCEEDED = "cost_tolerance_exceeded"
    INCONCLUSIVE = "inconclusive"
    CANDIDATE_INVALID = "candidate_invalid"
    EVALUATION_ERROR = "evaluation_error"


TaskId = Annotated[str, StringConstraints(pattern=r"^IR-(TR|VA|TE)-[0-9]{2}$")]
ExperimentId = Annotated[
    str,
    StringConstraints(pattern=r"^exp-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}$"),
]
RunId = Annotated[str, StringConstraints(pattern=r"^run-[a-f0-9]{16}$")]
Version = Annotated[str, StringConstraints(pattern=r"^v[0-9]{3}$")]
CandidateId = Annotated[str, StringConstraints(pattern=r"^cand-[a-f0-9]{12}$")]


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Task(FrozenModel):
    id: TaskId
    input: str
    available_tools: list[str]
    expected_outcome: dict
    invariants: list[str]
    split: Split
    max_calls: int = Field(ge=1)


class ToolCall(FrozenModel):
    tool: str
    arguments: dict
    result: dict
    timestamp: str


class Trajectory(FrozenModel):
    experiment_id: ExperimentId
    task_id: TaskId
    skill_version: Version | None
    messages: list[dict]
    tool_calls: list[ToolCall]
    final_output: dict | str
    tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    outcome: Outcome


class VerificationResult(FrozenModel):
    success: bool
    checks: dict[str, bool]
    evidence: dict


class CandidateSkill(FrozenModel):
    candidate_id: CandidateId
    parent_version: Version
    candidate_version: Version
    generation: int = Field(ge=1)
    status: MutationStatus = MutationStatus.PROPOSED
    failure_analysis: str
    procedural_change: str
    candidate_markdown: str
    rationale: str


class PromotionDecision(FrozenModel):
    candidate_id: CandidateId
    parent_version: Version
    candidate_version: Version
    decision: Decision
    reason_codes: list[RejectionReason] = Field(default_factory=list)
    evidence: dict = Field(default_factory=dict)


class RunRecord(FrozenModel):
    run_id: RunId
    experiment_id: ExperimentId
    task_id: TaskId
    split: Split
    condition_name: Condition
    skill_version: Version | None
    model_id: str | None = None
    run_slot: int = Field(ge=0)
    seed: int
    outcome: Outcome
    success: bool
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    trajectory: Trajectory
    verification: VerificationResult
    error_text: str | None = None
