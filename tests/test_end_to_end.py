import csv
import hashlib
import json
import sqlite3
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from skill_lab.cli import app
from skill_lab.config import DEFAULT_CONFIG

ROOT = Path(__file__).resolve().parents[1]
RUNNER = CliRunner()


def _config(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    payload = deepcopy(DEFAULT_CONFIG)
    payload["dataset_path"] = str(ROOT / "datasets" / "incident_tasks.json")
    payload["fixtures_path"] = str(ROOT / "datasets" / "tool_world.json")
    payload["skills_root"] = str(ROOT / "skills")
    payload["artifacts_root"] = str(tmp_path / "artifacts")
    payload["database_path"] = str(tmp_path / "experiments.sqlite3")
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _fixed_cli_clock(monkeypatch: object) -> None:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return datetime(2025, 1, 1, tzinfo=UTC)

    monkeypatch.setattr("skill_lab.cli.datetime", FixedDateTime)


def _run_ablation(tmp_path: Path, monkeypatch: object) -> Path:
    _fixed_cli_clock(monkeypatch)
    output = tmp_path / "ablation"
    result = RUNNER.invoke(
        app,
        [
            "ablate",
            "--skill",
            "incident-response",
            "--generations",
            "2",
            "--runs-per-task",
            "1",
            "--config",
            str(_config(tmp_path)),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    return output


def _artifact_hashes(root: Path, normalized_root: str) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "manifest.json":
            continue
        content = path.read_bytes().replace(str(root.parent).encode(), normalized_root.encode())
        hashes[path.relative_to(root).as_posix()] = hashlib.sha256(content).hexdigest()
    return hashes


def _assert_held_out_conditions(root: Path, evolved: str) -> None:
    report = (root / "report.md").read_text(encoding="utf-8")
    held_out = report.split("## Held-Out Results", 1)[1].split("## Skill Lineage", 1)[0]
    rows = [
        [cell.strip() for cell in line.strip("|").split("|")]
        for line in held_out.splitlines()
        if line.startswith("| ") and not line.startswith("|---") and "Condition" not in line
    ]
    assert {row[0] for row in rows} == {"no_skill", "seed", evolved}
    assert {row[1] for row in rows} == {"test"}


def test_offline_mini_evolution_promotes_and_rejects(tmp_path: Path, monkeypatch: object) -> None:
    _fixed_cli_clock(monkeypatch)
    output = tmp_path / "evolve"
    config = _config(tmp_path)
    validation = RUNNER.invoke(app, ["validate-data", "--config", str(config)])

    assert validation.exit_code == 0, validation.output
    assert validation.output.strip() == "valid"

    result = RUNNER.invoke(
        app,
        [
            "evolve",
            "--skill",
            "incident-response",
            "--generations",
            "2",
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
    model_line = next(line for line in result.output.splitlines() if line.startswith("model_id: "))
    assert model_line.removeprefix("model_id: ").startswith("mock-")

    database = sqlite3.connect(tmp_path / "experiments.sqlite3")
    try:
        decisions = database.execute(
            "SELECT candidate_version, decision FROM promotion_decisions ORDER BY candidate_version"
        ).fetchall()
        rows = database.execute("SELECT trajectory_json, estimated_cost_usd FROM runs").fetchall()
    finally:
        database.close()

    assert decisions == [("v002", "promote"), ("v003", "reject")]
    assert rows
    assert all(json.loads(trajectory)["tool_calls"] for trajectory, _ in rows)
    assert all(isinstance(cost, (int, float)) and cost >= 0 for _, cost in rows)

    artifact = _run_ablation(tmp_path, monkeypatch)
    assert (artifact / "report.md").is_file()
    assert (artifact / "naive" / "report.md").is_file()
    _assert_held_out_conditions(artifact, "evolved_verified")
    _assert_held_out_conditions(artifact / "naive", "evolved_naive")

    report = RUNNER.invoke(app, ["report", "--experiment", str(artifact)])
    assert report.exit_code == 0, report.output
    assert "## Held-Out Results" in report.output

    rerun = RUNNER.invoke(app, ["rerun", "--experiment", str(artifact)])
    assert rerun.exit_code == 0, rerun.output
    assert "byte-identical" in rerun.output


def test_offline_ablation_is_deterministic(tmp_path: Path, monkeypatch: object) -> None:
    first = _run_ablation(tmp_path / "first", monkeypatch)
    second = _run_ablation(tmp_path / "second", monkeypatch)

    _assert_held_out_conditions(first, "evolved_verified")
    _assert_held_out_conditions(first / "naive", "evolved_naive")
    _assert_held_out_conditions(second, "evolved_verified")
    _assert_held_out_conditions(second / "naive", "evolved_naive")
    assert (first / "naive").is_dir()
    assert (second / "naive").is_dir()

    assert _artifact_hashes(first, "<ROOT>") == _artifact_hashes(second, "<ROOT>")
    assert _artifact_hashes(first / "naive", "<ROOT>") == _artifact_hashes(
        second / "naive", "<ROOT>"
    )
    for root in (first, first / "naive"):
        with (root / "trajectories.jsonl").open(encoding="utf-8") as handle:
            trajectories = [json.loads(line) for line in handle if line.strip()]
        assert trajectories and all(isinstance(item["trajectory"], dict) for item in trajectories)
        with (root / "results.csv").open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert rows and all(float(row["estimated_cost_usd"]) >= 0 for row in rows)
