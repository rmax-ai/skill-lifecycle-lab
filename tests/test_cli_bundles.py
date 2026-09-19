import csv
import json
from copy import deepcopy
from pathlib import Path

import pytest
from typer.testing import CliRunner

from skill_lab.cli import _run_ablation_artifact, app
from skill_lab.config import DEFAULT_CONFIG
from skill_lab.experiment import ExperimentResult, GenerationRecord
from skill_lab.models import Decision, MutationStatus
from skill_lab.skills import load_skill

ROOT = Path(__file__).resolve().parents[1]
RUNNER = CliRunner()
EXPERIMENT_ID = "exp-20000101T000000Z-00000000"


def _config(tmp_path: Path) -> Path:
    payload = deepcopy(DEFAULT_CONFIG)
    payload["dataset_path"] = str(ROOT / "datasets" / "incident_tasks.json")
    payload["fixtures_path"] = str(ROOT / "datasets" / "tool_world.json")
    payload["skills_root"] = str(ROOT / "skills")
    payload["artifacts_root"] = str(tmp_path / "artifacts")
    payload["database_path"] = str(tmp_path / "experiments.sqlite3")
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _run_evolve(tmp_path: Path) -> tuple[Path, Path]:
    output = tmp_path / "evolve"
    config = _config(tmp_path)
    result = RUNNER.invoke(
        app,
        [
            "evolve",
            "--skill",
            "incident-response",
            "--generations",
            "1",
            "--runs-per-task",
            "1",
            "--mode",
            "verified",
            "--config",
            str(config),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    return output, config


def _manifest_paths(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }


def test_evolve_writes_durable_bundle(tmp_path: Path) -> None:
    output, _config_path = _run_evolve(tmp_path)

    payload = json.loads((output / "config.json").read_text(encoding="utf-8"))
    branch = payload["branches"]["verified"]

    assert payload["artifact_kind"] == "evolution"
    assert payload["command"] == {
        "generations": 1,
        "mode": "verified",
        "runs_per_task": 1,
        "skill": "incident-response",
    }
    assert branch["path"] == "."
    assert branch["final_version"] == "v002"
    assert branch["model_id"] == "mock-incident-v1"
    assert (output / "results.csv").is_file()
    assert (output / "trajectories.jsonl").read_text(encoding="utf-8")
    assert (output / "prompts.jsonl").read_text(encoding="utf-8")
    assert (output / "skills" / "incident-response" / "v001" / "SKILL.md").is_file()
    assert (output / "skills" / "incident-response" / "v002" / "SKILL.md").is_file()
    assert (output / "manifest.json").is_file()


def test_baseline_writes_durable_bundle(tmp_path: Path) -> None:
    output = tmp_path / "baseline"
    result = RUNNER.invoke(
        app,
        [
            "baseline",
            "--condition",
            "seed",
            "--runs-per-task",
            "1",
            "--config",
            str(_config(tmp_path)),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads((output / "config.json").read_text(encoding="utf-8"))
    assert payload["artifact_kind"] == "baseline"
    assert payload["command"] == {"condition": "seed", "runs_per_task": 1}
    with (output / "results.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    trajectories = (output / "trajectories.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == len(trajectories)
    assert (output / "skills" / "incident-response" / "v001" / "SKILL.md").is_file()
    assert (output / "manifest.json").is_file()


def test_bundle_manifest_covers_all_files(tmp_path: Path) -> None:
    output, _config_path = _run_evolve(tmp_path)
    payload = json.loads((output / "manifest.json").read_text(encoding="utf-8"))

    assert set(payload["files"]) == _manifest_paths(output)


def test_rerun_dispatches_evolution_kind(tmp_path: Path) -> None:
    output, _config_path = _run_evolve(tmp_path)

    result = RUNNER.invoke(app, ["rerun", "--experiment", str(output)])

    assert result.exit_code == 0, result.output
    assert "byte-identical" in result.output


def test_ablation_refuses_incomplete_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _config(tmp_path)
    settings_payload = json.loads(config_path.read_text(encoding="utf-8"))
    from skill_lab.config import AppConfig

    settings = AppConfig.model_validate(settings_payload)
    seed = load_skill(ROOT / "skills", "incident-response", "v001")
    generation = GenerationRecord(
        generation=1,
        parent_version="v001",
        candidate_version="v002",
        mutation_status=MutationStatus.PROPOSED,
        decision=Decision.PROMOTE,
        evidence={"candidate_id": "cand-000000000001"},
    )
    result = ExperimentResult(
        experiment_id=EXPERIMENT_ID,
        mode="verified",
        generations=[generation],
        final_skill=seed,
        prompt_records=[
            {
                "generation": 1,
                "candidate_id": "cand-000000000001",
                "request": {},
                "response_content": None,
            }
        ],
        skill_versions=[seed],
    )
    monkeypatch.setattr(
        "skill_lab.cli.run_ablation",
        lambda **_kwargs: {
            "verified": result,
            "naive": result.model_copy(update={"mode": "naive"}),
        },
    )

    with pytest.raises(ValueError, match="candidate skill is missing"):
        _run_ablation_artifact(
            settings=settings,
            skill="incident-response",
            generations=1,
            runs_per_task=1,
            experiment_id=EXPERIMENT_ID,
            output_path=tmp_path / "ablation",
            allow_live=False,
        )
