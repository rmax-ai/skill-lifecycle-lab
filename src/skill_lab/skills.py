"""Load and write versioned Markdown skills."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from skill_lab.models import CandidateSkill, Version

_FRONT_MATTER_KEYS = frozenset({"name", "version", "description"})
_METADATA_KEYS = frozenset({"parent", "created_by", "generation", "status"})
_VERSION_PATTERN = re.compile(r"^v[0-9]{3}$")


class SkillMetadata(BaseModel):
    """The lineage metadata persisted beside a skill."""

    model_config = ConfigDict(extra="forbid", strict=True)

    parent: Version | None
    created_by: str = Field(min_length=1)
    generation: int = Field(ge=0)
    status: str = Field(min_length=1)

    def __getitem__(self, key: str) -> object:
        return getattr(self, key)

    def get(self, key: str, default: object = None) -> object:
        return getattr(self, key, default)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return self.model_dump(mode="json") == dict(other)
        return super().__eq__(other)


class Skill(BaseModel):
    """A loaded skill and its versioned lineage metadata."""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1)
    version: Version
    description: str = Field(min_length=1)
    markdown: str = Field(min_length=1)
    metadata: SkillMetadata

    @property
    def skill_markdown(self) -> str:
        """Return the complete contents of ``SKILL.md``."""

        return self.markdown

    @property
    def content(self) -> str:
        return self.markdown

    @property
    def parent(self) -> str | None:
        return self.metadata.parent

    @property
    def created_by(self) -> str:
        return self.metadata.created_by

    @property
    def generation(self) -> int:
        return self.metadata.generation

    @property
    def status(self) -> str:
        return self.metadata.status

    @property
    def front_matter(self) -> dict[str, str | int]:
        """Return the normalized front matter values."""

        return {
            "name": self.name,
            "version": int(self.version[1:]),
            "description": self.description,
        }


def _expanded(path: Path | str) -> Path:
    return Path(os.path.expanduser(str(path)))


def _validate_name(name: str) -> None:
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ValueError("skill name must be a single non-empty path component")


def _validate_version(version: str) -> None:
    if not _VERSION_PATTERN.fullmatch(version):
        raise ValueError("skill version must use the vNNN format")


def _parse_front_matter(markdown: str) -> tuple[dict[str, str], str]:
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md is missing front matter")

    try:
        closing_index = next(
            index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"
        )
    except StopIteration as error:
        raise ValueError("SKILL.md has unterminated front matter") from error

    front_matter: dict[str, str] = {}
    for line in lines[1:closing_index]:
        if not line.strip():
            continue
        if ":" not in line:
            raise ValueError("SKILL.md has invalid front matter")
        key, value = line.split(":", 1)
        key = key.strip()
        if not key or key in front_matter:
            raise ValueError("SKILL.md has duplicate or empty front matter keys")
        front_matter[key] = value.strip()

    if set(front_matter) != _FRONT_MATTER_KEYS:
        raise ValueError("SKILL.md has an invalid front matter schema")
    if any(not front_matter[key] for key in _FRONT_MATTER_KEYS):
        raise ValueError("SKILL.md front matter values must not be empty")

    body = "\n".join(lines[closing_index + 1 :])
    return front_matter, body


def _front_matter_version(value: str) -> int:
    normalized = value.strip()
    if normalized.isdigit():
        return int(normalized)
    raise ValueError("SKILL.md front matter version is invalid")


def _load_metadata(path: Path) -> SkillMetadata:
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("skill metadata is not readable JSON") from error

    if not isinstance(payload, dict) or set(payload) != _METADATA_KEYS:
        raise ValueError("skill metadata has an invalid schema")
    try:
        return SkillMetadata.model_validate(payload)
    except ValidationError as error:
        raise ValueError("skill metadata has invalid fields") from error


def load_skill(root: Path, name: str, version: str) -> Skill:
    """Load one skill and verify its directory, Markdown, and metadata versions agree."""

    _validate_name(name)
    _validate_version(version)
    skill_dir = _expanded(root) / name / version
    skill_path = skill_dir / "SKILL.md"
    metadata_path = skill_dir / "metadata.json"

    try:
        markdown = skill_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ValueError(f"cannot read skill Markdown: {skill_path}") from error

    front_matter, _ = _parse_front_matter(markdown)
    if front_matter["name"] != name:
        raise ValueError("skill name does not match its directory")
    if _front_matter_version(front_matter["version"]) != int(version[1:]):
        raise ValueError("skill version does not match its directory")

    metadata = _load_metadata(metadata_path)
    return Skill(
        name=name,
        version=version,
        description=front_matter["description"],
        markdown=markdown,
        metadata=metadata,
    )


def _candidate_markdown(
    markdown: str,
    name: str,
    version: str,
    description: str | None,
    parent: Skill | None,
) -> str:
    try:
        front_matter, body = _parse_front_matter(markdown)
    except ValueError as error:
        if markdown.lstrip().startswith("---"):
            raise
        front_matter = {}
        body = markdown
        if description is None and parent is None:
            raise ValueError(
                "candidate Markdown without front matter requires a description or parent"
            ) from error

    if front_matter and front_matter["name"] != name:
        raise ValueError("candidate skill name does not match its directory")
    candidate_description = description or front_matter.get("description")
    if candidate_description is None and parent is not None:
        candidate_description = parent.description
    if not candidate_description:
        raise ValueError("candidate skill requires a non-empty description")
    if "\n" in candidate_description or "\r" in candidate_description:
        raise ValueError("candidate description must be one line")

    return (
        "---\n"
        f"name: {name}\n"
        f"version: {int(version[1:])}\n"
        f"description: {candidate_description}\n"
        "---\n"
        f"{body}\n"
    )


def write_candidate(
    root: Path,
    name: str | Skill | CandidateSkill | None = None,
    version: int | str | Skill | CandidateSkill | None = None,
    parent: Skill | str | CandidateSkill | None = None,
    candidate_markdown: str | None = None,
    *,
    candidate: CandidateSkill | None = None,
    parent_skill: Skill | None = None,
    candidate_version: str | None = None,
    parent_version: str | None = None,
    markdown: str | None = None,
    description: str | None = None,
    created_by: str = "mutation",
    generation: int | None = None,
    status: str = "proposed",
) -> Path:
    """Write a candidate without changing the parent skill.

    ``version`` and ``candidate_version`` are aliases, as are
    ``candidate_markdown`` and ``markdown``.  The aliases make the function
    usable with both the directory vocabulary and the candidate model
    vocabulary while keeping one canonical on-disk representation.
    """

    candidate_model = candidate
    if parent_skill is not None:
        if parent is not None and (
            not isinstance(parent, Skill) or parent.version != parent_skill.version
        ):
            raise ValueError("parent versions do not match")
        parent = parent_skill

    if isinstance(name, CandidateSkill):
        if candidate_model is not None and candidate_model != name:
            raise ValueError("candidate values do not match")
        candidate_model = name
        name = _candidate_name(candidate_model.candidate_markdown)
    elif isinstance(name, Skill):
        if parent is None:
            parent = name
        name = name.name

    if isinstance(version, (Skill, CandidateSkill)):
        if isinstance(version, CandidateSkill):
            candidate_model = version
        elif parent is None:
            parent = version
        version = None
    elif isinstance(version, int):
        version = f"v{version:03d}"
    elif (
        isinstance(version, str)
        and not _VERSION_PATTERN.fullmatch(version)
        and candidate_markdown is None
    ):
        candidate_markdown = version
        version = None
    if isinstance(parent, CandidateSkill):
        if candidate_model is not None and candidate_model != parent:
            raise ValueError("candidate values do not match")
        candidate_model = parent
        parent = None

    if candidate_model is not None:
        if version is not None and version != candidate_model.candidate_version:
            raise ValueError("candidate versions do not match")
        if version is None:
            version = candidate_model.candidate_version
        if candidate_markdown is None:
            candidate_markdown = candidate_model.candidate_markdown
        if parent is None:
            parent = candidate_model.parent_version
        elif (
            parent.version if isinstance(parent, Skill) else parent
        ) != candidate_model.parent_version:
            raise ValueError("parent versions do not match")
        if generation is None:
            generation = candidate_model.generation
        if status == "proposed":
            status = candidate_model.status.value

    if (
        candidate_markdown is None
        and isinstance(parent, str)
        and not _VERSION_PATTERN.fullmatch(parent)
    ):
        candidate_markdown = parent
        parent = None

    if name is None:
        if isinstance(parent, Skill):
            name = parent.name
        elif candidate_model is not None:
            name = _candidate_name(candidate_model.candidate_markdown)
    if not isinstance(name, str):
        raise ValueError("candidate skill name is required")
    _validate_name(name)
    parent_skill = parent if isinstance(parent, Skill) else None
    if version is None:
        version = candidate_version
    if version is None:
        parent_version_for_default = (
            parent_skill.version if parent_skill is not None else parent or parent_version
        )
        if parent_version_for_default is not None and _VERSION_PATTERN.fullmatch(
            parent_version_for_default
        ):
            version = f"v{int(parent_version_for_default[1:]) + 1:03d}"
        elif generation is not None:
            version = f"v{generation + 1:03d}"
    elif candidate_version is not None and version != candidate_version:
        raise ValueError("candidate versions do not match")
    if version is None:
        raise ValueError("candidate version is required")
    _validate_version(version)

    if candidate_markdown is None:
        candidate_markdown = markdown
    elif markdown is not None and candidate_markdown != markdown:
        raise ValueError("candidate Markdown values do not match")
    if candidate_markdown is None:
        raise ValueError("candidate Markdown is required")

    resolved_parent = parent_skill.version if parent_skill is not None else parent
    if parent_skill is not None and parent_skill.name != name:
        raise ValueError("parent skill name does not match candidate name")
    if parent_version is not None:
        _validate_version(parent_version)
        if resolved_parent is not None and resolved_parent != parent_version:
            raise ValueError("parent versions do not match")
        resolved_parent = parent_version

    if generation is None:
        generation = (
            parent_skill.generation + 1
            if parent_skill is not None
            else max(0, int(version[1:]) - 1)
        )
    metadata = SkillMetadata(
        parent=resolved_parent,
        created_by=created_by,
        generation=generation,
        status=status,
    )
    normalized_markdown = _candidate_markdown(
        candidate_markdown,
        name,
        version,
        description,
        parent_skill,
    )

    candidate_dir = _expanded(root) / name / version
    candidate_dir.mkdir(parents=True, exist_ok=False)
    (candidate_dir / "SKILL.md").write_text(normalized_markdown, encoding="utf-8")
    metadata_json = json.dumps(metadata.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    (candidate_dir / "metadata.json").write_text(metadata_json, encoding="utf-8")
    return candidate_dir


def _candidate_name(markdown: str) -> str:
    front_matter, _ = _parse_front_matter(markdown)
    return front_matter["name"]
