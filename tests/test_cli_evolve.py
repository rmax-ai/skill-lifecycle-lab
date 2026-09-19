import json
from copy import deepcopy
from pathlib import Path

from typer.testing import CliRunner

from skill_lab.cli import app
from skill_lab.config import DEFAULT_CONFIG

ROOT = Path(__file__).resolve().parents[1]
RUNNER = CliRunner()


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


def test_evolve_cli_mock(tmp_path: Path) -> None:
    output = tmp_path / "evolve"
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
            str(_config(tmp_path)),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert f"artifact_path: {output}" in result.output
    assert "model_id: mock-incident-v1" in result.output
    assert "SKILL_LAB_API_KEY" not in result.output
    assert (output / "skills" / "incident-response" / "v002" / "metadata.json").exists()


def test_baseline_cli_mock(tmp_path: Path) -> None:
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
    assert f"artifact_path: {output}" in result.output
    assert "model_id: mock-incident-v1" in result.output
    assert "SKILL_LAB_API_KEY" not in result.output
    assert output.exists()
    assert (tmp_path / "experiments.sqlite3").exists()


def test_invalid_cli_value_exits_two() -> None:
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
            "unsupported",
        ],
    )

    assert result.exit_code == 2
