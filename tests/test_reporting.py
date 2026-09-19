import csv
import json
from pathlib import Path

from skill_lab.experiment import ExperimentResult, GenerationRecord
from skill_lab.metrics import EvaluationSummary
from skill_lab.models import (
    Decision,
    MutationStatus,
    RunRecord,
    Trajectory,
    VerificationResult,
)
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


def _run(
    run_id: str,
    task_id: str,
    split: str,
    *,
    model_id: str | None = None,
) -> RunRecord:
    condition = "evolved_verified" if split != "test" else "evolved_verified"
    skill_version = "v002" if split == "test" else "v001"
    trajectory = Trajectory(
        experiment_id=EXPERIMENT_ID,
        task_id=task_id,
        skill_version=skill_version,
        messages=[],
        tool_calls=[],
        final_output={},
        tokens=0,
        latency_ms=0,
        outcome="success",
    )
    return RunRecord(
        run_id=run_id,
        experiment_id=EXPERIMENT_ID,
        task_id=task_id,
        split=split,
        condition_name=condition,
        skill_version=skill_version,
        model_id=model_id,
        run_slot=0,
        seed=1729,
        outcome="success",
        success=True,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        latency_ms=0,
        estimated_cost_usd=0.0,
        trajectory=trajectory,
        verification=VerificationResult(success=True, checks={}, evidence={}),
    )


def _held_out_summary() -> EvaluationSummary:
    return EvaluationSummary(
        experiment_id=EXPERIMENT_ID,
        split="test",
        condition_name="evolved_verified",
        skill_version="v002",
        task_count=1,
        run_count=3,
        successful_runs=2,
        success_rate=2 / 3,
        failed_runs=1,
        outcome_counts={"success": 2, "failure": 1},
    )


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
    assert (artifact / "results.csv").read_text(encoding="utf-8").splitlines()[0] == (
        "experiment_id,condition,split,task_id,run_slot,skill_version,model_id,outcome,"
        "success,tool_calls,invalid_tool_calls,prohibited_actions,unnecessary_actions,steps,"
        "input_tokens,output_tokens,total_tokens,latency_ms,estimated_cost_usd"
    )
    assert RESULT_COLUMNS[-1] == "estimated_cost_usd"


def test_canonical_artifact_bytes(tmp_path: Path) -> None:
    result = _result(tmp_path)
    first = write_artifact(result, tmp_path / "first")
    second = write_artifact(result, tmp_path / "second")

    assert _artifact_files(first) == _artifact_files(second)
    generation_payload = json.loads((first / "generations.json").read_text(encoding="utf-8"))
    generation = GenerationArtifact.model_validate(generation_payload["generations"][1])
    assert GenerationArtifact.model_validate_json(generation.model_dump_json()) == generation
    manifest = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["files"]) == set(_artifact_files(first)) - {"manifest.json"}


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


def test_results_csv_includes_all_runs_once(tmp_path: Path) -> None:
    train = _run("run-0000000000000001", "IR-TR-01", "train", model_id="mock-incident-v1")
    validation = _run("run-0000000000000002", "IR-VA-01", "validation")
    held_out = _run("run-0000000000000003", "IR-TE-01", "test", model_id="mock-incident-v1")
    result = _result(tmp_path).model_copy(
        update={
            "runs": [train, validation],
            "held_out_runs": [held_out, train],
        }
    )

    artifact = write_artifact(result, tmp_path / "artifact")
    with (artifact / "results.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 3
    assert [row["task_id"] for row in rows] == ["IR-TE-01", "IR-TR-01", "IR-VA-01"]
    assert [row["model_id"] for row in rows] == [
        "mock-incident-v1",
        "mock-incident-v1",
        "",
    ]


def test_prompts_jsonl_contains_exact_exchange(tmp_path: Path) -> None:
    exchange = {
        "generation": 1,
        "candidate_id": "cand-000000000001",
        "request": {
            "kind": "mutation",
            "current_skill": {"version": "v001"},
            "train_failures": [{"task_id": "IR-TR-01"}],
        },
        "response_content": '{"candidate_id":"cand-000000000001"}',
    }
    result = _result(tmp_path).model_copy(update={"prompt_records": [exchange]})

    artifact = write_artifact(result, tmp_path / "artifact")
    lines = (artifact / "prompts.jsonl").read_text(encoding="utf-8").splitlines()

    assert len(lines) == 1
    assert json.loads(lines[0]) == exchange


def test_skill_lineage_versions_written(tmp_path: Path) -> None:
    result = _result(tmp_path)
    seed = load_skill(SKILLS, "incident-response", "v001")
    rejected_dir = write_candidate(
        tmp_path / "source-skills",
        parent_skill=result.final_skill,
        candidate_version="v003",
        candidate_markdown=result.final_skill.markdown,
        rationale="Rejected candidate rationale.",
        status="rejected",
    )
    rejected = load_skill(rejected_dir.parents[1], "incident-response", "v003")
    result = result.model_copy(update={"skill_versions": [seed, rejected]})

    artifact = write_artifact(result, tmp_path / "artifact")
    skill_root = artifact / "skills" / "incident-response"

    assert sorted(path.name for path in skill_root.iterdir()) == ["v001", "v002", "v003"]
    for version in ("v001", "v002", "v003"):
        assert (skill_root / version / "SKILL.md").is_file()
        assert (skill_root / version / "metadata.json").is_file()
    assert (skill_root / "v003" / "RATIONALE.md").read_text(encoding="utf-8") == (
        "Rejected candidate rationale.\n"
    )


def test_held_out_rows_not_duplicated(tmp_path: Path) -> None:
    result = _result(tmp_path).model_copy(
        update={
            "baseline_summaries": [_held_out_summary()],
            "held_out_summaries": [_held_out_summary()],
        }
    )

    artifact = write_artifact(result, tmp_path / "artifact")
    report = (artifact / "report.md").read_text(encoding="utf-8")
    held_out_section = report.split("## Held-Out Results", 1)[1].split("## Skill Lineage", 1)[0]

    assert held_out_section.count("| Condition | Split | Skill |") == 1
    assert held_out_section.count("evolved_verified") == 1


def test_conclusion_reflects_supplied_held_out(tmp_path: Path) -> None:
    result = _result(tmp_path).model_copy(update={"held_out_summaries": [_held_out_summary()]})

    artifact = write_artifact(result, tmp_path / "artifact")
    report = (artifact / "report.md").read_text(encoding="utf-8")
    conclusions = report.split("## Interpretation/Conclusions", 1)[1]

    assert "Held-out results include 1 observed summary rows covering 3 observed runs." in report
    assert "unavailable" not in conclusions
    assert "descriptive" in conclusions
    assert "scripted mock" in conclusions
    assert "Observed prompt records: 0" in report
