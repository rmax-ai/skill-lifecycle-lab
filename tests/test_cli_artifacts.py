import hashlib
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


def _files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _run_ablation(tmp_path: Path) -> tuple[Path, Path]:
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
    return output, _config(tmp_path)


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


def test_generate_example_cli(tmp_path: Path, monkeypatch: object) -> None:
    config = _config(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = RUNNER.invoke(app, ["generate-example", "--config", str(config)])

    assert result.exit_code == 0, result.output
    artifact = tmp_path / "artifacts" / "example-mock"
    assert artifact.is_dir()
    assert (artifact / "report.md").is_file()
    assert (artifact / "naive" / "report.md").is_file()
    assert "model_id: mock-incident-v1" in result.output
    payload = json.loads((artifact / "config.json").read_text(encoding="utf-8"))
    assert payload["experiment_id"] == "exp-20000101T000000Z-deadbeef"


def test_rerun_mock_is_byte_identical(tmp_path: Path) -> None:
    output, config = _run_ablation(tmp_path)
    before = _files(output)

    result = RUNNER.invoke(app, ["rerun", "--experiment", str(output)])

    assert result.exit_code == 0, result.output
    assert "byte-identical" in result.output
    assert _files(output) == before
    assert config.is_file()


def test_live_rerun_requires_flag(tmp_path: Path) -> None:
    output, _config_path = _run_ablation(tmp_path)
    config_path = output / "config.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["configuration"]["model"]["provider"] = "openai_compatible"
    config_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _refresh_manifest(output)

    result = RUNNER.invoke(app, ["rerun", "--experiment", str(output)])

    assert result.exit_code == 1
    assert "live rerun requires --allow-live" in result.output
