"""Train-only skill mutation proposals.

Mutation is deliberately a one-way boundary.  This module can read the current
skill and failed training evidence, and can ask a model for a candidate.  It
does not receive task collections, evaluation summaries, stores, or promotion
helpers, and it never changes the current skill.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from skill_lab.agent import ChatModel
from skill_lab.models import (
    CandidateSkill,
    MutationStatus,
    RunRecord,
    Split,
    TaskId,
    Trajectory,
    VerificationResult,
)
from skill_lab.skills import Skill

_RESPONSE_FIELDS = frozenset(
    {"failure_analysis", "procedural_change", "candidate_markdown", "rationale"}
)
_LEGACY_RESPONSE_FIELDS = frozenset(
    {
        "candidate_id",
        "parent_version",
        "candidate_version",
        "generation",
        "status",
        "failure_analysis",
        "procedural_change",
        "candidate_markdown",
        "rationale",
    }
)
_NON_TRAIN_TASK_ID = re.compile(r"\bIR-(?:VA|TE)-[0-9]{2}\b")
_NON_TRAIN_MARKER = re.compile(r"(?i)(?<![a-z])(?:validation|test)(?![a-z])")
_VERSION_LINE = re.compile(r"^\s*version\s*:\s*(?P<version>v?[0-9]{1,3})\s*$")
_NAME_LINE = re.compile(r"^\s*name\s*:\s*(?P<name>\S.*?)\s*$")


class MutationConfig(BaseModel):
    """Limits for the proposal prompt.

    The default leaves the selected failure set intact.  A caller may set
    ``max_failures`` to retain only the first sorted packets, which keeps the
    prompt boundary deterministic without changing the source evidence.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    max_failures: int | None = Field(default=None, ge=1)


class TrainFailurePacket(BaseModel):
    """The only execution evidence accepted by the mutation prompt.

    ``split`` is accepted as a construction-time assertion for callers that
    are converting a run record, but it is intentionally not a persisted or
    serialized field.  The packet exposed to the model therefore contains only
    the task id, task input, trajectory, and verifier result.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    task_id: TaskId
    task_input: str = Field(min_length=1)
    trajectory: Trajectory
    verification: VerificationResult

    @model_validator(mode="before")
    @classmethod
    def _assert_train_split(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        split = value.get("split", Split.TRAIN)
        split_value = split.value if isinstance(split, Split) else split
        if split_value != Split.TRAIN.value:
            raise ValueError("mutation packets must have split='train'")
        packet = dict(value)
        packet.pop("split", None)
        return packet

    @field_validator("task_id")
    @classmethod
    def _assert_train_task_id(cls, value: TaskId) -> TaskId:
        if not value.startswith("IR-TR-"):
            raise ValueError("mutation packets may contain training task IDs only")
        return value

    @model_validator(mode="after")
    def _reject_non_train_references(self) -> TrainFailurePacket:
        serialized = _canonical_json(self.model_dump(mode="json"))
        if _NON_TRAIN_TASK_ID.search(serialized) or _NON_TRAIN_MARKER.search(serialized):
            raise ValueError("mutation packets may not contain validation or test evidence")
        return self

    @property
    def split(self) -> Literal["train"]:
        """Expose the asserted source split without serializing it."""

        return "train"

    @classmethod
    def from_run(cls, run: RunRecord, *, task_input: str | None = None) -> TrainFailurePacket:
        """Build a packet from one failed training run."""

        if run.split is not Split.TRAIN:
            raise ValueError("mutation packets may be built from train runs only")
        if run.success:
            raise ValueError("successful runs are not mutation failures")
        if run.trajectory.task_id != run.task_id:
            raise ValueError("run and trajectory task IDs do not match")
        resolved_input = task_input if task_input is not None else _task_input(run.trajectory)
        return cls(
            split=run.split,
            task_id=run.task_id,
            task_input=resolved_input,
            trajectory=run.trajectory,
            verification=run.verification,
        )

    @classmethod
    def from_record(cls, run: RunRecord, *, task_input: str | None = None) -> TrainFailurePacket:
        """Compatibility alias for :meth:`from_run`."""

        return cls.from_run(run, task_input=task_input)


class MutationResponse(BaseModel):
    """The exact four content fields returned by a mutation model."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    failure_analysis: str = Field(min_length=1)
    procedural_change: str = Field(min_length=1)
    candidate_markdown: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class CandidateResponseError(ValueError):
    """Raised when a model response is not a valid four-field proposal."""


def select_train_failures(
    train_runs: Sequence[RunRecord],
    task_inputs: Mapping[str, str] | None = None,
) -> list[TrainFailurePacket]:
    """Select failed training runs in stable task/slot order.

    Every input run is checked before filtering.  A validation or test run is
    therefore rejected rather than silently ignored, which prevents callers
    from accidentally mixing splits at this boundary.
    """

    resolved_inputs = dict(task_inputs or {})
    _assert_train_inputs(resolved_inputs)
    runs = list(train_runs)
    for run in runs:
        if not isinstance(run, RunRecord):
            raise TypeError("mutation selection requires RunRecord values")
        if run.split is not Split.TRAIN:
            raise ValueError("mutation selection accepts train runs only")
        if not run.task_id.startswith("IR-TR-"):
            raise ValueError("mutation selection accepts training task IDs only")

    failed_runs = sorted(
        (run for run in runs if not run.success),
        key=lambda run: (run.task_id, run.run_slot, run.run_id),
    )
    return [
        TrainFailurePacket.from_run(run, task_input=resolved_inputs.get(run.task_id))
        for run in failed_runs
    ]


def select_failed_train_packets(
    train_runs: Sequence[RunRecord],
    task_inputs: Mapping[str, str] | None = None,
) -> list[TrainFailurePacket]:
    """Explicit alias for :func:`select_train_failures`."""

    return select_train_failures(train_runs, task_inputs=task_inputs)


def build_mutation_prompt(
    current_skill: Skill,
    train_failures: Sequence[TrainFailurePacket],
    config: MutationConfig | None = None,
) -> dict[str, Any]:
    """Build the model request from current Markdown and train packets only."""

    if not isinstance(current_skill, Skill):
        raise TypeError("current_skill must be a Skill")
    resolved_config = _config(config)
    packets = _sorted_packets(train_failures)
    if resolved_config.max_failures is not None:
        packets = packets[: resolved_config.max_failures]

    return {
        "kind": "mutation",
        "current_skill": {
            "name": current_skill.name,
            "version": current_skill.version,
            "description": current_skill.description,
            "markdown": current_skill.markdown,
        },
        "train_failures": [packet.model_dump(mode="json") for packet in packets],
    }


def build_prompt(
    current_skill: Skill,
    train_failures: Sequence[TrainFailurePacket],
    config: MutationConfig | None = None,
) -> dict[str, Any]:
    """Compatibility alias for :func:`build_mutation_prompt`."""

    return build_mutation_prompt(current_skill, train_failures, config=config)


def propose_mutation(
    current_skill: Skill,
    train_failures: list[TrainFailurePacket],
    model: ChatModel,
    config: MutationConfig | None = None,
) -> CandidateSkill:
    """Ask the model for a proposed candidate without changing skill status.

    A response is either the exact four-field mutation body or the complete
    typed envelope emitted by the existing offline model.  In the latter case
    only the same four content fields are trusted; candidate identity and
    status are checked/derived locally, and a model-supplied promoted status is
    never honored.
    """

    prompt = build_mutation_prompt(current_skill, train_failures, config=config)
    try:
        response = model.complete(prompt)
    except Exception as error:
        raise CandidateResponseError("mutation model response could not be obtained") from error

    payload = _response_payload(response)
    mutation_response, envelope = _parse_response(payload, current_skill)
    candidate_version = _candidate_version(
        mutation_response.candidate_markdown,
        current_skill,
        envelope,
    )
    generation = envelope.generation if envelope is not None else current_skill.generation + 1
    if generation != current_skill.generation + 1:
        raise CandidateResponseError("candidate generation does not follow its parent")

    candidate_id = (
        envelope.candidate_id
        if envelope is not None
        else _candidate_id(current_skill, candidate_version, mutation_response)
    )
    return CandidateSkill(
        candidate_id=candidate_id,
        parent_version=current_skill.version,
        candidate_version=candidate_version,
        generation=generation,
        status=MutationStatus.PROPOSED,
        failure_analysis=mutation_response.failure_analysis,
        procedural_change=mutation_response.procedural_change,
        candidate_markdown=mutation_response.candidate_markdown,
        rationale=mutation_response.rationale,
    )


def parse_candidate_response(
    current_skill: Skill,
    response: object,
) -> CandidateSkill:
    """Parse a response payload without performing another model call."""

    payload = _response_payload(response)
    mutation_response, envelope = _parse_response(payload, current_skill)
    candidate_version = _candidate_version(
        mutation_response.candidate_markdown,
        current_skill,
        envelope,
    )
    generation = envelope.generation if envelope is not None else current_skill.generation + 1
    if generation != current_skill.generation + 1:
        raise CandidateResponseError("candidate generation does not follow its parent")
    candidate_id = (
        envelope.candidate_id
        if envelope is not None
        else _candidate_id(current_skill, candidate_version, mutation_response)
    )
    return CandidateSkill(
        candidate_id=candidate_id,
        parent_version=current_skill.version,
        candidate_version=candidate_version,
        generation=generation,
        status=MutationStatus.PROPOSED,
        failure_analysis=mutation_response.failure_analysis,
        procedural_change=mutation_response.procedural_change,
        candidate_markdown=mutation_response.candidate_markdown,
        rationale=mutation_response.rationale,
    )


def _config(config: MutationConfig | None) -> MutationConfig:
    if config is None:
        return MutationConfig()
    if isinstance(config, MutationConfig):
        return config
    return MutationConfig.model_validate(config)


def _sorted_packets(
    train_failures: Sequence[TrainFailurePacket],
) -> list[TrainFailurePacket]:
    packets: list[TrainFailurePacket] = []
    for value in train_failures:
        try:
            packet = (
                value
                if isinstance(value, TrainFailurePacket)
                else (TrainFailurePacket.model_validate(value))
            )
        except (TypeError, ValidationError, ValueError) as error:
            raise ValueError("mutation prompt accepts train failure packets only") from error
        if packet.split != "train":
            raise ValueError("mutation prompt accepts train failure packets only")
        packets.append(packet)
    return sorted(
        packets,
        key=lambda packet: (packet.task_id, _canonical_json(packet.model_dump(mode="json"))),
    )


def _assert_train_inputs(task_inputs: Mapping[str, str]) -> None:
    for task_id, task_input in task_inputs.items():
        if not isinstance(task_id, str) or not task_id.startswith("IR-TR-"):
            raise ValueError("task input mappings may contain training task IDs only")
        if not isinstance(task_input, str) or not task_input:
            raise ValueError("task inputs must be non-empty strings")
        if _NON_TRAIN_TASK_ID.search(task_input) or _NON_TRAIN_MARKER.search(task_input):
            raise ValueError("task inputs may not contain validation or test evidence")


def _task_input(trajectory: Trajectory) -> str:
    for message in trajectory.messages:
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            return message["content"]
    raise ValueError("failed trajectory does not contain a task input")


def _response_payload(response: object) -> dict[str, Any]:
    value: object = response
    if isinstance(response, BaseModel):
        if isinstance(getattr(response, "content", None), str):
            value = response.content
        else:
            value = response.model_dump(mode="json")
    elif isinstance(response, Mapping):
        if isinstance(response.get("content"), str):
            value = response["content"]
        elif isinstance(response.get("output"), Mapping):
            value = response["output"]

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise CandidateResponseError("mutation response is not JSON") from error
    if not isinstance(value, Mapping):
        raise CandidateResponseError("mutation response must be a JSON object")
    return {str(key): item for key, item in value.items()}


def _parse_response(
    payload: Mapping[str, Any],
    current_skill: Skill,
) -> tuple[MutationResponse, CandidateSkill | None]:
    keys = set(payload)
    if keys == _RESPONSE_FIELDS:
        try:
            return MutationResponse.model_validate(dict(payload)), None
        except ValidationError as error:
            raise CandidateResponseError("mutation response has invalid content fields") from error

    if keys != _LEGACY_RESPONSE_FIELDS:
        raise CandidateResponseError("mutation response must contain exactly four fields")

    try:
        envelope = CandidateSkill.model_validate(dict(payload))
    except ValidationError as error:
        raise CandidateResponseError("mutation response has invalid candidate fields") from error
    if envelope.parent_version != current_skill.version:
        raise CandidateResponseError("candidate parent does not match the current skill")
    try:
        mutation_response = MutationResponse.model_validate(
            {field: getattr(envelope, field) for field in _RESPONSE_FIELDS}
        )
    except ValidationError as error:
        raise CandidateResponseError("mutation response has invalid content fields") from error
    return mutation_response, envelope


def _candidate_version(
    markdown: str,
    current_skill: Skill,
    envelope: CandidateSkill | None,
) -> str:
    version = envelope.candidate_version if envelope is not None else _markdown_version(markdown)
    if version is None:
        version = f"v{int(current_skill.version[1:]) + 1:03d}"
    if envelope is not None:
        markdown_version = _markdown_version(markdown)
        if markdown_version is not None and markdown_version != version:
            raise CandidateResponseError("candidate Markdown and version disagree")
    if int(version[1:]) <= int(current_skill.version[1:]):
        raise CandidateResponseError("candidate version must follow its parent")
    _validate_candidate_name(markdown, current_skill.name)
    return version


def _markdown_version(markdown: str) -> str | None:
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        closing = next(
            index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"
        )
    except StopIteration as error:
        raise CandidateResponseError("candidate Markdown has unterminated front matter") from error

    version: str | None = None
    for line in lines[1:closing]:
        match = _VERSION_LINE.fullmatch(line)
        if match is None:
            continue
        if version is not None:
            raise CandidateResponseError("candidate Markdown has duplicate version fields")
        raw = match.group("version")
        number = int(raw.removeprefix("v"))
        version = f"v{number:03d}"
    if version is None:
        raise CandidateResponseError("candidate Markdown has no version field")
    return version


def _validate_candidate_name(markdown: str, expected_name: str) -> None:
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return
    try:
        closing = next(
            index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"
        )
    except StopIteration as error:
        raise CandidateResponseError("candidate Markdown has unterminated front matter") from error
    names = [_NAME_LINE.fullmatch(line) for line in lines[1:closing]]
    values = [match.group("name") for match in names if match is not None]
    if values and values[0] != expected_name:
        raise CandidateResponseError("candidate Markdown name does not match its parent")


def _candidate_id(
    current_skill: Skill,
    candidate_version: str,
    response: MutationResponse,
) -> str:
    material = {
        "candidate_markdown": response.candidate_markdown,
        "candidate_version": candidate_version,
        "failure_analysis": response.failure_analysis,
        "parent_version": current_skill.version,
        "procedural_change": response.procedural_change,
        "rationale": response.rationale,
    }
    digest = hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()[:12]
    return f"cand-{digest}"


def _canonical_json(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True)
