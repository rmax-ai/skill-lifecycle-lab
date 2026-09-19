import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
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


def _evolution_bundle(tmp_path: Path) -> Path:
    output = tmp_path / "evolution"
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
            str(_config(tmp_path)),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    return output


def _files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _refresh_manifest(root: Path) -> None:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for relative, entry in manifest["files"].items():
        path = root / relative
        entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        entry["size"] = path.stat().st_size
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_held_out_command_writes_subtree_for_evolution_bundle(tmp_path: Path) -> None:
    output = _evolution_bundle(tmp_path)
    parent_before = {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in output.iterdir()
        if path.name != "held-out" and path.is_file()
    }

    result = RUNNER.invoke(app, ["held-out", "--experiment", str(output)])

    assert result.exit_code == 0, result.output
    held_out = output / "held-out"
    assert (held_out / "config.json").is_file()
    assert (held_out / "held-out.json").is_file()
    assert (held_out / "results.csv").is_file()
    assert (held_out / "trajectories.jsonl").is_file()
    assert (held_out / "report.md").is_file()
    assert (held_out / "manifest.json").is_file()
    config = json.loads((held_out / "config.json").read_text(encoding="utf-8"))
    assert config["artifact_kind"] == "held-out"
    assert set(config["branches"]) == {"verified"}
    assert "mode" in (held_out / "results.csv").read_text(encoding="utf-8").splitlines()[0]
    report = (held_out / "report.md").read_text(encoding="utf-8")
    assert "Observed Results" in report
    assert "Interpretation/Conclusions" in report
    assert "test set is used only here" in report
    assert parent_before == {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in output.iterdir()
        if path.name != "held-out" and path.is_file()
    }


def test_held_out_command_verifies_before_model_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _evolution_bundle(tmp_path)
    config_path = output / "config.json"
    config_path.write_text(config_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    called = False

    def fail_model_creation(*_args: object, **_kwargs: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("model must not be created after verification failure")

    monkeypatch.setattr("skill_lab.cli.create_chat_model", fail_model_creation)
    result = RUNNER.invoke(app, ["held-out", "--experiment", str(output)])

    assert result.exit_code == 1
    assert "manifest hash mismatch" in result.output
    assert not called


def test_held_out_command_is_deterministic(tmp_path: Path) -> None:
    output = _evolution_bundle(tmp_path)
    first = RUNNER.invoke(
        app,
        ["held-out", "--experiment", str(output), "--runs-per-task", "2"],
    )
    assert first.exit_code == 0, first.output
    first_files = _files(output / "held-out")

    second = RUNNER.invoke(
        app,
        ["held-out", "--experiment", str(output), "--runs-per-task", "2"],
    )

    assert second.exit_code == 0, second.output
    assert _files(output / "held-out") == first_files


def test_held_out_requires_allow_live_for_live_provider(tmp_path: Path) -> None:
    output = _evolution_bundle(tmp_path)
    config_path = output / "config.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["configuration"]["model"]["provider"] = "openai_compatible"
    config_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _refresh_manifest(output)

    result = RUNNER.invoke(app, ["held-out", "--experiment", str(output)])

    assert result.exit_code == 1
    assert "live held-out evaluation requires --allow-live" in result.output
