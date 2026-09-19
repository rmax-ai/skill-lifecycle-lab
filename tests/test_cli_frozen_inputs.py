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


def _live_inputs(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "live-inputs"
    root.mkdir(exist_ok=True)
    dataset = root / "incident_tasks.json"
    fixtures = root / "tool_world.json"
    if not dataset.exists():
        dataset.write_bytes((ROOT / "datasets" / "incident_tasks.json").read_bytes())
    if not fixtures.exists():
        fixtures.write_bytes((ROOT / "datasets" / "tool_world.json").read_bytes())
    return dataset, fixtures


def _config(tmp_path: Path) -> Path:
    dataset, fixtures = _live_inputs(tmp_path)
    payload = deepcopy(DEFAULT_CONFIG)
    payload["dataset_path"] = str(dataset)
    payload["fixtures_path"] = str(fixtures)
    payload["skills_root"] = str(ROOT / "skills")
    payload["artifacts_root"] = str(tmp_path / "artifacts")
    payload["database_path"] = str(tmp_path / "experiments.sqlite3")
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _run_ablation(tmp_path: Path) -> Path:
    output = tmp_path / "ablation"
    result = RUNNER.invoke(
        app,
        [
            "ablate",
            "--skill",
            "incident-response",
            "--generations",
            "1",
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


def _run_evolution(tmp_path: Path) -> Path:
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


def test_bundle_freezes_inputs_with_hashes(tmp_path: Path) -> None:
    output = _run_ablation(tmp_path)
    config = json.loads((output / "config.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    dataset, fixtures = _live_inputs(tmp_path)

    assert set(config["inputs"]) == {"dataset", "fixtures"}
    for name, source in (("dataset", dataset), ("fixtures", fixtures)):
        declaration = config["inputs"][name]
        frozen = output / declaration["path"]
        assert declaration["path"] == f"inputs/{source.name}"
        assert frozen.read_bytes() == source.read_bytes()
        assert declaration["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
        assert declaration["size"] == source.stat().st_size
        assert declaration["path"] in manifest["files"]
        assert manifest["files"][declaration["path"]]["sha256"] == declaration["sha256"]
        assert manifest["files"][declaration["path"]]["size"] == declaration["size"]


def test_rerun_aborts_before_model_creation_on_input_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _run_ablation(tmp_path)
    config = json.loads((output / "config.json").read_text(encoding="utf-8"))
    frozen_dataset = output / config["inputs"]["dataset"]["path"]
    frozen_dataset.write_bytes(frozen_dataset.read_bytes() + b"\n")
    _refresh_manifest(output)

    called = False

    def fail_model_creation(*_args: object, **_kwargs: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("model must not be created after frozen input failure")

    monkeypatch.setattr("skill_lab.cli.create_chat_model", fail_model_creation)
    result = RUNNER.invoke(app, ["rerun", "--experiment", str(output)])

    assert result.exit_code == 1
    assert "frozen input hash mismatch" in result.output
    assert not called


def test_rerun_uses_frozen_inputs(tmp_path: Path) -> None:
    output = _run_ablation(tmp_path)
    before = {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in output.rglob("*")
        if path.is_file()
    }
    dataset, fixtures = _live_inputs(tmp_path)
    dataset.write_text("{ live dataset was changed }\n", encoding="utf-8")
    fixtures.write_text("{ live fixtures were changed }\n", encoding="utf-8")

    result = RUNNER.invoke(app, ["rerun", "--experiment", str(output)])

    assert result.exit_code == 0, result.output
    assert "byte-identical" in result.output
    assert {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in output.rglob("*")
        if path.is_file()
    } == before


def test_held_out_uses_frozen_inputs(tmp_path: Path) -> None:
    output = _run_evolution(tmp_path)
    dataset, fixtures = _live_inputs(tmp_path)
    dataset.write_text("{ live dataset was changed }\n", encoding="utf-8")
    fixtures.write_text("{ live fixtures were changed }\n", encoding="utf-8")

    result = RUNNER.invoke(app, ["held-out", "--experiment", str(output)])

    assert result.exit_code == 0, result.output
    held_out = output / "held-out"
    config = json.loads((held_out / "config.json").read_text(encoding="utf-8"))
    for name in ("dataset", "fixtures"):
        declaration = config["inputs"][name]
        frozen = held_out / declaration["path"]
        parent_frozen = output / "inputs" / Path(declaration["path"]).name
        assert frozen.read_bytes() == parent_frozen.read_bytes()
        assert declaration["sha256"] == hashlib.sha256(frozen.read_bytes()).hexdigest()
        assert declaration["size"] == frozen.stat().st_size
