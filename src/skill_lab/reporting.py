"""Canonical, descriptive artifacts for one skill evolution result."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from collections import defaultdict
from collections.abc import Mapping, Sequence
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from skill_lab.experiment import ExperimentResult, GenerationRecord
from skill_lab.models import RunRecord

ARTIFACT_FILES = (
    "config.json",
    "results.csv",
    "generations.json",
    "report.md",
    "manifest.json",
    "trajectories.jsonl",
    "prompts.jsonl",
)

RESULT_COLUMNS = (
    "experiment_id",
    "condition",
    "split",
    "task_id",
    "run_slot",
    "skill_version",
    "model_id",
    "outcome",
    "success",
    "tool_calls",
    "invalid_tool_calls",
    "prohibited_actions",
    "unnecessary_actions",
    "steps",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "latency_ms",
    "estimated_cost_usd",
)

GENERATION_FIELDS = (
    "generation",
    "parent_version",
    "candidate_version",
    "mutation_status",
    "train_failure_task_ids",
    "validation_parent",
    "validation_candidate",
    "decision",
    "reason_codes",
    "evidence",
)

_ARTIFACT_SCHEMA = "skill-lab-artifact-v1"


class GenerationArtifact(BaseModel):
    """The frozen JSON shape for one generation row."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    generation: int = Field(ge=1)
    parent_version: str
    candidate_version: str | None = None
    mutation_status: str
    train_failure_task_ids: list[str] = Field(default_factory=list)
    validation_parent: dict[str, Any] | None = None
    validation_candidate: dict[str, Any] | None = None
    decision: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_record(cls, record: GenerationRecord) -> GenerationArtifact:
        """Convert the evolution record without dropping negative generations."""

        return cls.model_validate(record.model_dump(mode="json"))


def write_artifact(result: ExperimentResult, root: Path) -> Path:
    """Write the canonical artifact tree for an evolution result.

    The current evolution result contains aggregate training summaries rather
    than run records.  When a caller supplies optional run or prompt records,
    those records are serialized into the corresponding raw-evidence files;
    otherwise the files remain valid, deterministic empty JSONL streams and
    ``results.csv`` contains its frozen header only.
    """

    if not isinstance(result, ExperimentResult):
        raise TypeError("result must be an ExperimentResult")

    output_root = _expanded(root)
    output_root.mkdir(parents=True, exist_ok=True)
    _write_json(output_root / "config.json", _configuration(result))
    _write_text(output_root / "results.csv", _results_csv(result))
    _write_json(output_root / "generations.json", _generations_payload(result))
    _write_text(output_root / "report.md", _report(result))
    _write_text(output_root / "trajectories.jsonl", _trajectories_jsonl(result))
    _write_text(output_root / "prompts.jsonl", _prompts_jsonl(result))
    _write_skill(output_root, result)
    _write_manifest(output_root, result.experiment_id)
    return output_root


def _configuration(result: ExperimentResult) -> dict[str, Any]:
    configuration: dict[str, Any] = {
        "artifact_schema": _ARTIFACT_SCHEMA,
        "experiment_id": result.experiment_id,
        "mode": result.mode,
        "generation_count": len(result.generations),
        "final_skill": {
            "name": result.final_skill.name,
            "version": result.final_skill.version,
            "generation": result.final_skill.generation,
            "status": result.final_skill.status,
        },
    }
    supplied = _optional_value(result, ("configuration", "config"))
    if isinstance(supplied, Mapping):
        configuration["configuration"] = _jsonable(supplied)
    return configuration


def _generations_payload(result: ExperimentResult) -> dict[str, Any]:
    records = [
        GenerationArtifact.from_record(record)
        for record in sorted(result.generations, key=_generation_sort_key)
    ]
    return {
        "experiment_id": result.experiment_id,
        "mode": result.mode,
        "generations": [record.model_dump(mode="json") for record in records],
    }


def _generation_sort_key(record: GenerationRecord) -> tuple[int, str, str]:
    return (record.generation, record.parent_version, record.candidate_version or "")


def _results_csv(result: ExperimentResult) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=RESULT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for run in _run_records(result):
        writer.writerow(_run_row(run))
    return stream.getvalue()


def _run_row(run: RunRecord) -> dict[str, Any]:
    evidence = run.verification.evidence
    return {
        "experiment_id": run.experiment_id,
        "condition": _enum_value(run.condition_name),
        "split": _enum_value(run.split),
        "task_id": run.task_id,
        "run_slot": run.run_slot,
        "skill_version": run.skill_version or "",
        "model_id": run.model_id or "",
        "outcome": _enum_value(run.outcome),
        "success": int(run.success),
        "tool_calls": len(run.trajectory.tool_calls),
        "invalid_tool_calls": _evidence_length(evidence, "tool_errors"),
        "prohibited_actions": _evidence_length(evidence, "forbidden_actions"),
        "unnecessary_actions": _evidence_length(evidence, "unnecessary_actions"),
        "steps": sum(
            isinstance(message, Mapping) and message.get("role") == "assistant"
            for message in run.trajectory.messages
        ),
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "total_tokens": run.total_tokens,
        "latency_ms": run.latency_ms,
        "estimated_cost_usd": _number(run.estimated_cost_usd),
    }


def _run_records(result: ExperimentResult) -> list[RunRecord]:
    records: dict[str, RunRecord] = {}
    for candidate in (*result.runs, *result.held_out_runs):
        run = candidate if isinstance(candidate, RunRecord) else RunRecord.model_validate(candidate)
        records.setdefault(run.run_id, run)
    return sorted(
        records.values(),
        key=lambda run: (
            _enum_value(run.condition_name),
            _enum_value(run.split),
            run.task_id,
            run.run_slot,
            run.run_id,
        ),
    )


def _flatten_records(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        flattened: list[object] = []
        for key in sorted(value, key=str):
            flattened.extend(_flatten_records(value[key]))
        return flattened
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        flattened = []
        for item in value:
            flattened.extend(_flatten_records(item))
        return flattened
    return [value]


def _trajectories_jsonl(result: ExperimentResult) -> str:
    lines: list[str] = []
    for run in _run_records(result):
        payload = {
            "condition": _enum_value(run.condition_name),
            "experiment_id": run.experiment_id,
            "outcome": _enum_value(run.outcome),
            "run_slot": run.run_slot,
            "skill_version": run.skill_version,
            "split": _enum_value(run.split),
            "success": run.success,
            "task_id": run.task_id,
            "trajectory": run.trajectory.model_dump(mode="json"),
            "verification": run.verification.model_dump(mode="json"),
        }
        lines.append(_canonical_line(payload))
    return "".join(f"{line}\n" for line in lines)


def _prompts_jsonl(result: ExperimentResult) -> str:
    return "".join(f"{_canonical_line(record)}\n" for record in result.prompt_records)


def _write_skill(root: Path, result: ExperimentResult) -> None:
    skills = {
        (skill.name, skill.version): skill for skill in (*result.skill_versions, result.final_skill)
    }
    for skill in sorted(skills.values(), key=_skill_sort_key):
        skill_dir = root / "skills" / skill.name / skill.version
        skill_dir.mkdir(parents=True, exist_ok=True)
        _write_text(skill_dir / "SKILL.md", skill.markdown)
        _write_json(skill_dir / "metadata.json", skill.metadata.model_dump(mode="json"))
        if skill.rationale is not None:
            _write_text(skill_dir / "RATIONALE.md", f"{skill.rationale.rstrip(chr(10))}\n")


def _skill_sort_key(skill: Any) -> tuple[str, int, str]:
    return skill.name, int(skill.version[1:]), skill.version


def _write_manifest(root: Path, experiment_id: str) -> None:
    files: dict[str, dict[str, Any]] = {}
    for path in _files_for_manifest(root):
        relative = path.relative_to(root).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files[relative] = {"sha256": digest, "size": path.stat().st_size}
    _write_json(
        root / "manifest.json",
        {
            "artifact_schema": _ARTIFACT_SCHEMA,
            "experiment_id": experiment_id,
            "files": files,
        },
    )


def _files_for_manifest(root: Path) -> list[Path]:
    paths = [root / name for name in ARTIFACT_FILES if name != "manifest.json"]
    skill_root = root / "skills"
    if skill_root.exists():
        paths.extend(path for path in skill_root.rglob("*") if path.is_file())
    return sorted(path for path in paths if path.is_file())


def _report(result: ExperimentResult) -> str:
    generations = [
        GenerationArtifact.from_record(record)
        for record in sorted(result.generations, key=_generation_sort_key)
    ]
    baselines = _summary_records(result, ("baseline_summaries",))
    held_out = _summary_records(result, ("held_out_summaries",))
    observed = _summary_records(result, ("train_summaries",))

    lines = [
        "# Experiment",
        "",
        "## Configuration",
        "",
        "| Field | Observed value |",
        "|---|---|",
        f"| experiment_id | `{_md(result.experiment_id)}` |",
        f"| mode | `{_md(result.mode)}` |",
        f"| final skill | `{_md(result.final_skill.name)} {_md(result.final_skill.version)}` |",
        f"| generations requested and recorded | `{len(result.generations)}` |",
        "",
        "## Baselines",
        "",
    ]
    lines.extend(_summary_section(baselines, "No baseline summaries were supplied."))
    lines.extend(
        [
            "",
            "## Evolution History",
            "",
            "| Generation | Parent | Candidate | Validation Lift | Regression | Decision |",
            "|---:|---|---|---:|---:|---|",
        ]
    )
    if generations:
        for generation in generations:
            lines.append(
                "| "
                + " | ".join(
                    (
                        str(generation.generation),
                        _md(generation.parent_version),
                        _md(generation.candidate_version or "n/a"),
                        _md(_format_lift(generation)),
                        _md(_format_regression(generation)),
                        _md(_generation_decision(generation)),
                    )
                )
                + " |"
            )
    else:
        lines.append("| n/a | n/a | n/a | n/a | n/a | no generations recorded |")
    lines.extend(
        [
            "",
            "## Held-Out Results",
            "",
        ]
    )
    lines.extend(_summary_section(held_out, "No held-out summaries were supplied."))
    lines.extend(
        [
            "",
            "## Skill Lineage",
            "",
            "The tree retains promoted, rejected, and generation-error outcomes.",
            "",
            "```text",
            *_lineage_lines(result, generations),
            "```",
            "",
            f"Final selected skill: `{_md(result.final_skill.version)}`.",
            "",
            "## Failure Analysis",
            "",
        ]
    )
    lines.extend(_failure_analysis(generations))
    lines.extend(
        [
            "",
            "## Observed Results",
            "",
            "All statements in this section describe supplied records and are labeled observed.",
        ]
    )
    if observed:
        lines.extend(_summary_table(observed))
    else:
        lines.append("No aggregate training summaries were supplied.")
    lines.extend(
        [
            "",
            f"Observed generation records: `{len(generations)}`.",
            f"Observed prompt records: {len(result.prompt_records)}",
            f"Observed final skill: `{_md(result.final_skill.version)}`.",
            "",
            "## Interpretation/Conclusions",
            "",
            "- This artifact is descriptive; it does not establish causality between a mutation "
            "and any observed metric.",
            "- A scripted mock result, when present, describes the offline harness and is not "
            "evidence of general model performance.",
        ]
    )
    if held_out:
        held_out_runs = sum(
            _integer_value(summary, "run_count", _value(summary, "runs", 0)) for summary in held_out
        )
        lines.append(
            f"- Held-out results include {len(held_out)} observed summary rows "
            f"covering {held_out_runs} observed runs."
        )
    else:
        lines.append(
            "- Held-out interpretation is unavailable when no held-out summaries are supplied."
        )
    return "\n".join(lines) + "\n"


def _summary_records(result: ExperimentResult, attributes: Sequence[str]) -> list[object]:
    records: list[object] = []
    for attribute in attributes:
        records.extend(_flatten_records(_optional_value(result, (attribute,))))
    return sorted(records, key=_summary_sort_key)


def _summary_sort_key(summary: object) -> tuple[str, str, str]:
    return (
        str(_value(summary, "condition_name", _value(summary, "condition", ""))),
        str(_value(summary, "split", "")),
        str(_value(summary, "skill_version", "")),
    )


def _summary_section(summaries: Sequence[object], empty_message: str) -> list[str]:
    if not summaries:
        return [empty_message]
    return _summary_table(summaries)


def _summary_table(summaries: Sequence[object]) -> list[str]:
    lines = [
        "| Condition | Split | Skill | Success | Runs | Tool Calls | Tokens | Violations |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for summary in summaries:
        prohibited = _integer_value(summary, "prohibited_actions")
        unnecessary = _integer_value(summary, "unnecessary_actions")
        invalid = _integer_value(summary, "invalid_tool_calls")
        condition = _enum_value(_value(summary, "condition_name", _value(summary, "condition")))
        lines.append(
            "| "
            + " | ".join(
                (
                    _md(condition),
                    _md(_enum_value(_value(summary, "split"))),
                    _md(_value(summary, "skill_version", "n/a") or "n/a"),
                    _md(_format_number(_value(summary, "success_rate", 0.0))),
                    str(_integer_value(summary, "run_count", _value(summary, "runs", 0))),
                    str(_integer_value(summary, "tool_calls")),
                    str(_integer_value(summary, "total_tokens")),
                    str(prohibited + unnecessary + invalid),
                )
            )
            + " |"
        )
    return lines


def _failure_analysis(generations: Sequence[GenerationArtifact]) -> list[str]:
    negative = [generation for generation in generations if _is_negative(generation)]
    if not negative:
        return ["No rejected or generation-error generations were recorded."]

    lines = ["Negative generations and rejection evidence were retained:"]
    for generation in negative:
        reasons = ", ".join(generation.reason_codes) or "no reason code supplied"
        failures = ", ".join(generation.train_failure_task_ids) or "none recorded"
        candidate = generation.candidate_version or "no candidate"
        lines.append(
            f"- Generation `{generation.generation}`: candidate `{candidate}`, "
            f"decision `{_generation_decision(generation)}`, reasons `{_md(reasons)}`, "
            f"train failures `{_md(failures)}`."
        )
    return lines


def _lineage_lines(
    result: ExperimentResult,
    generations: Sequence[GenerationArtifact],
) -> list[str]:
    children: dict[str, list[GenerationArtifact]] = defaultdict(list)
    for generation in generations:
        children[generation.parent_version].append(generation)
    for values in children.values():
        values.sort(key=lambda value: (value.generation, value.candidate_version or ""))

    root = result.final_skill.parent
    if root is None:
        root = generations[0].parent_version if generations else result.final_skill.version
    lines: list[str] = []
    rendered: set[tuple[str, int, str | None]] = set()

    def render(version: str, prefix: str = "") -> None:
        lines.append(f"{prefix}{version}")
        for index, generation in enumerate(children.get(version, [])):
            identity = (
                generation.parent_version,
                generation.generation,
                generation.candidate_version,
            )
            if identity in rendered:
                continue
            rendered.add(identity)
            is_last = index == len(children[version]) - 1
            connector = "└── " if is_last else "├── "
            label = generation.candidate_version or f"generation-{generation.generation}"
            decision = _generation_decision(generation)
            if decision == "reject":
                decision = "rejected"
            label = f"{label} {decision}"
            if _is_negative(generation):
                label += " (negative generation)"
            lines.append(f"{prefix}{connector}{label}")
            if generation.candidate_version is not None:
                next_prefix = prefix + ("    " if is_last else "│   ")
                render(generation.candidate_version, next_prefix)

    render(root)
    known_versions = {root}
    known_versions.update(
        generation.candidate_version
        for generation in generations
        if generation.candidate_version is not None
    )
    for generation in generations:
        if generation.parent_version not in known_versions:
            lines.append(
                f"{generation.parent_version} (additional lineage root for generation "
                f"{generation.generation})"
            )
            render(generation.parent_version, "")
            known_versions.add(generation.parent_version)
    return lines


def _is_negative(generation: GenerationArtifact) -> bool:
    return generation.decision == "reject" or generation.mutation_status in {
        "rejected",
        "generation_error",
    }


def _generation_decision(generation: GenerationArtifact) -> str:
    return generation.decision or generation.mutation_status


def _format_lift(generation: GenerationArtifact) -> str:
    evidence = generation.evidence
    direct = _first_number(
        evidence.get("validation_lift"),
        evidence.get("skill_lift"),
        _nested_value(evidence, "rates", "validation_lift"),
        _nested_value(evidence, "rates", "skill_lift"),
    )
    if direct is not None:
        return _format_number(direct)
    parent = _number_from(generation.validation_parent, "success_rate")
    candidate = _number_from(generation.validation_candidate, "success_rate")
    if parent is None or candidate is None:
        return "n/a"
    return _format_number(candidate - parent)


def _format_regression(generation: GenerationArtifact) -> str:
    direct = _first_number(
        _number_from(generation.validation_candidate, "regression_rate"),
        generation.evidence.get("regression_rate"),
        _nested_value(generation.evidence, "rates", "candidate_regression_rate"),
    )
    return "n/a" if direct is None else _format_number(direct)


def _first_number(*values: object) -> float | int | None:
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return value
    return None


def _number_from(value: Mapping[str, Any] | None, key: str) -> float | int | None:
    if not isinstance(value, Mapping):
        return None
    return _first_number(value.get(key))


def _nested_value(value: object, *keys: str) -> object:
    current = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _evidence_length(evidence: Mapping[str, Any], key: str) -> int:
    value = evidence.get(key, [])
    return len(value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else 0


def _integer_value(value: object, key: str, default: object = 0) -> int:
    candidate = _value(value, key, default)
    return candidate if isinstance(candidate, int) and not isinstance(candidate, bool) else 0


def _value(value: object, key: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _optional_value(value: object, keys: Sequence[str]) -> object:
    for key in keys:
        if isinstance(value, Mapping) and key in value:
            return value[key]
        try:
            candidate = getattr(value, key)
        except AttributeError:
            continue
        if candidate is not None:
            return candidate
    return None


def _enum_value(value: object) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value) if value is not None else ""


def _format_number(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "n/a"
    return f"{value:.6f}" if isinstance(value, float) else str(value)


def _number(value: object) -> object:
    if isinstance(value, float):
        return format(value, ".17g")
    return value


def _md(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _canonical_line(value: object) -> str:
    return json.dumps(_jsonable(value), sort_keys=True)


def _canonical_json(value: object) -> str:
    return json.dumps(_jsonable(value), indent=2, sort_keys=True) + "\n"


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _jsonable(value.model_dump(mode="json"))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_jsonable(item) for item in value]
    if isinstance(value, set):
        return sorted(_jsonable(item) for item in value)
    return value


def _write_json(path: Path, value: object) -> None:
    _write_text(path, _canonical_json(value))


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _expanded(path: Path | str) -> Path:
    return Path(os.path.expanduser(os.fspath(path)))
