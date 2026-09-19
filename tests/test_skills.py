import json
from pathlib import Path

import pytest

from skill_lab.skills import load_skill, write_candidate

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"


def test_loads_seed_skill() -> None:
    skill = load_skill(SKILLS, "incident-response", "v001")

    assert skill.name == "incident-response"
    assert skill.version == "v001"
    assert skill.description == "End-to-end procedure for handling customer incident tickets."
    assert skill.metadata["parent"] is None
    assert skill.metadata["created_by"] == "human"
    assert skill.metadata["generation"] == 0
    assert skill.metadata["status"] == "baseline"
    assert skill.front_matter["version"] == 1
    assert skill.model_validate_json(skill.model_dump_json()) == skill


def test_rejects_version_mismatch(tmp_path: Path) -> None:
    source = SKILLS / "incident-response" / "v001"
    skill_dir = tmp_path / "incident-response" / "v001"
    skill_dir.mkdir(parents=True)
    markdown = (
        (source / "SKILL.md").read_text(encoding="utf-8").replace("version: 1", "version: 2", 1)
    )
    (skill_dir / "SKILL.md").write_text(markdown, encoding="utf-8")
    (skill_dir / "metadata.json").write_text(
        (source / "metadata.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="version"):
        load_skill(tmp_path, "incident-response", "v001")


def test_candidate_lineage_preserved(tmp_path: Path) -> None:
    seed = load_skill(SKILLS, "incident-response", "v001")
    parent_metadata = seed.metadata.model_dump(mode="json")
    candidate_dir = write_candidate(
        tmp_path,
        "incident-response",
        "v002",
        seed,
        seed.markdown,
    )

    candidate = load_skill(tmp_path, "incident-response", "v002")
    assert candidate_dir == tmp_path / "incident-response" / "v002"
    assert candidate.parent == "v001"
    assert candidate.created_by == "mutation"
    assert candidate.generation == 1
    assert candidate.status == "proposed"
    assert seed.metadata.model_dump(mode="json") == parent_metadata
    assert json.loads((candidate_dir / "metadata.json").read_text(encoding="utf-8")) == {
        "created_by": "mutation",
        "generation": 1,
        "parent": "v001",
        "status": "proposed",
    }
