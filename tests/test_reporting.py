import json
from pathlib import Path

from skill_lab.experiment import ExperimentResult, GenerationRecord
from skill_lab.metrics import EvaluationSummary
from skill_lab.models import Decision, MutationStatus
from skill_lab.reporting import RESULT_COLUMNS, GenerationArtifact, write_artifact
from skill_lab.skills import load_skill, write_candidate

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
EXPERIMENT_ID = "exp-20000101T000000Z-00000000"


def _result(tmp_path: Path) -> ExperimentResult:
    seed = load_skill(SKILLS, "incident-response", "v001")
    skill_root = tmp_path / "source-skills"
    write_candidate(
        skill_root,
        parent_skill=seed,
        candidate_version="v002",
        candidate_markdown=seed.markdown,
        rationale="Placeholder promoted candidate rationale.",
        status="promoted",
    )
    final_skill = load_skill(skill_root, "incident-response", "v002")
    generations = [
        GenerationRecord(
            generation=1,
            parent_version="v001",
            candidate_version="v002",
            mutation_status=MutationStatus.PROMOTED,
            train_failure_task_ids=["IR-TR-01"],
            validation_parent={
                "success_rate": 0.5,
                "regression_rate": 0.0,
            },
            validation_candidate={
                "success_rate": 1.0,
                "regression_rate": 0.0,
            },
            decision=Decision.PROMOTE,
            evidence={"skill_lift": 0.5},
        ),
        GenerationRecord(
            generation=2,
            parent_version="v002",
            candidate_version="v003",
            mutation_status=MutationStatus.REJECTED,
            train_failure_task_ids=["IR-TR-02"],
            validation_parent={
                "success_rate": 1.0,
                "regression_rate": 0.0,
            },
            validation_candidate={
                "success_rate": 1.0,
                "regression_rate": 0.5,
            },
            decision=Decision.REJECT,
            reason_codes=["regression_threshold_exceeded"],
            evidence={"regression_rate": 0.5},
        ),
    ]
    summary = EvaluationSummary(
        experiment_id=EXPERIMENT_ID,
        split="train",
        condition_name="evolved_verified",
        skill_version="v002",
        task_count=2,
        run_count=2,
        successful_runs=1,
        success_rate=0.5,
        failed_runs=1,
        outcome_counts={"success": 1, "failure": 1},
    )
    return ExperimentResult(
        experiment_id=EXPERIMENT_ID,
        mode="verified",
        generations=generations,
        final_skill=final_skill,
        train_summaries=[summary],
    )


def _artifact_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_report_contains_required_sections(tmp_path: Path) -> None:
    artifact = write_artifact(_result(tmp_path), tmp_path / "artifact")
    report = (artifact / "report.md").read_text(encoding="utf-8")

    sections = (
        "Configuration",
        "Baselines",
        "Evolution History",
        "Held-Out Results",
        "Skill Lineage",
        "Failure Analysis",
        "Observed Results",
        "Interpretation/Conclusions",
    )
    assert all(f"## {section}" in report for section in sections)
    assert "causality" in report
    assert (artifact / "results.csv").read_text(encoding="utf-8").splitlines()[0] == ",".join(
        RESULT_COLUMNS
    )


def test_canonical_artifact_bytes(tmp_path: Path) -> None:
    result = _result(tmp_path)
    first = write_artifact(result, tmp_path / "first")
    second = write_artifact(result, tmp_path / "second")

    assert _artifact_files(first) == _artifact_files(second)
    generation_payload = json.loads((first / "generations.json").read_text(encoding="utf-8"))
    generation = GenerationArtifact.model_validate(generation_payload["generations"][1])
    assert GenerationArtifact.model_validate_json(generation.model_dump_json()) == generation


def test_report_retains_rejection(tmp_path: Path) -> None:
    artifact = write_artifact(_result(tmp_path), tmp_path / "artifact")
    generations = json.loads((artifact / "generations.json").read_text(encoding="utf-8"))
    report = (artifact / "report.md").read_text(encoding="utf-8")

    rejected = generations["generations"][1]
    assert rejected["generation"] == 2
    assert rejected["candidate_version"] == "v003"
    assert rejected["decision"] == "reject"
    assert "v003 rejected" in report
    assert "negative generation" in report
