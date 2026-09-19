from pathlib import Path

from skill_lab.skills import load_skill, write_candidate

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"


def test_rejected_candidate_is_preserved(tmp_path: Path) -> None:
    parent = load_skill(SKILLS, "incident-response", "v001")
    rationale = "The candidate was rejected after validation did not improve."

    candidate_dir = write_candidate(
        tmp_path,
        name=parent.name,
        version="v002",
        parent=parent,
        candidate_markdown=parent.markdown,
        rationale=rationale,
    )

    candidate = load_skill(tmp_path, "incident-response", "v002")

    assert candidate_dir == tmp_path / "incident-response" / "v002"
    assert candidate.parent == "v001"
    assert candidate.status == "proposed"
    assert candidate.rationale == rationale
    assert (candidate_dir / "RATIONALE.md").read_text(encoding="utf-8") == f"{rationale}\n"


def test_parent_metadata_unchanged(tmp_path: Path) -> None:
    parent = load_skill(SKILLS, "incident-response", "v001")
    parent_metadata = parent.metadata.model_dump(mode="json")
    parent_markdown = parent.markdown

    write_candidate(
        tmp_path,
        name=parent.name,
        version="v002",
        parent=parent,
        candidate_markdown=parent.markdown,
        rationale="The candidate was retained for lineage review.",
    )

    assert parent.metadata.model_dump(mode="json") == parent_metadata
    assert parent.markdown == parent_markdown
    assert load_skill(SKILLS, "incident-response", "v001").status == "baseline"
