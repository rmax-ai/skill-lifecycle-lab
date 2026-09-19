"""Command-line entry point for the skill lifecycle lab."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

import typer

from skill_lab.config import AppConfig, create_chat_model, default_config, load_config
from skill_lab.data_validation import validate_operator_inputs
from skill_lab.experiment import ablate as run_ablation
from skill_lab.experiment import evaluate_condition
from skill_lab.experiment import evolve as run_evolution
from skill_lab.models import Split
from skill_lab.reporting import write_artifact
from skill_lab.skills import Skill, load_skill
from skill_lab.storage import ExperimentStore
from skill_lab.tasks import load_tasks

app = typer.Typer(add_completion=False, no_args_is_help=True)

_ARTIFACT_SCHEMA = "skill-lab-artifact-v1"
_EXAMPLE_EXPERIMENT_ID = "exp-20000101T000000Z-deadbeef"


@app.callback()
def main() -> None:
    """Run skill lifecycle lab commands."""


@app.command("validate-data")
def validate_data(
    config: Annotated[Path, typer.Option("--config")] = Path("configs/default.json"),
) -> None:
    """Validate the configured dataset, fixtures, and seed skill."""

    settings = _load_cli_config(config)
    errors = validate_operator_inputs(
        settings.dataset_path,
        settings.fixtures_path,
        settings.skills_root / "incident-response" / "v001",
    )
    if errors:
        for error in errors:
            typer.echo(error, err=True)
        raise typer.Exit(code=1)
    typer.echo("valid")


@app.command("baseline")
def baseline(
    config: Annotated[Path, typer.Option("--config")] = Path("configs/default.json"),
    runs_per_task: Annotated[int, typer.Option("--runs-per-task", min=1)] = 1,
    condition: Annotated[Literal["no-skill", "seed"], typer.Option("--condition")] = "seed",
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    """Run one deterministic no-skill or seed baseline."""

    settings = _load_cli_config(config)
    output_path, experiment_id, created_at = _experiment_output(settings.artifacts_root, output)
    model = create_chat_model(settings)
    tasks = load_tasks(settings.dataset_path)
    seed_skill = (
        load_skill(settings.skills_root, "incident-response", "v001")
        if condition == "seed"
        else None
    )

    with ExperimentStore(settings.database_path) as store:
        store.insert_experiment(
            experiment_id=experiment_id,
            created_at=created_at,
            mode="baseline",
            config_json={
                "condition": condition,
                "runs_per_task": runs_per_task,
                "seed": settings.seed,
            },
            git_commit="unavailable",
            dataset_sha256="unavailable",
            model_id=settings.model.model,
            seed=settings.seed,
            runs_per_task=runs_per_task,
            status="completed",
        )
        evaluate_condition(
            tasks=tasks,
            fixtures=settings.fixtures_path,
            model=model,
            agent_config=settings.agent,
            experiment_id=experiment_id,
            split="train",
            condition_name=condition,
            runs_per_task=runs_per_task,
            base_seed=settings.seed,
            store=store,
            skill_markdown=seed_skill.markdown if seed_skill is not None else None,
            skill_version=seed_skill.version if seed_skill is not None else None,
            pricing=settings.pricing,
            config=settings,
        )

    _print_run_summary(output_path, settings.model.model)


@app.command("evolve")
def evolve(
    skill: Annotated[str, typer.Option("--skill")],
    generations: Annotated[int, typer.Option("--generations", min=1, max=20)],
    runs_per_task: Annotated[int, typer.Option("--runs-per-task", min=1)],
    mode: Annotated[Literal["verified", "naive"], typer.Option("--mode")],
    config: Annotated[Path, typer.Option("--config")] = Path("configs/default.json"),
    output: Annotated[Path | None, typer.Option("--output")] = None,
    allow_live: Annotated[bool, typer.Option("--allow-live")] = False,
) -> None:
    """Run bounded verified or naive skill evolution."""

    settings = _load_cli_config(config)
    output_path, experiment_id, _created_at = _experiment_output(settings.artifacts_root, output)
    model = create_chat_model(settings, allow_live=allow_live, env=os.environ)
    tasks = load_tasks(settings.dataset_path)
    evolution_tasks = [task for task in tasks if task.split != Split.TEST]
    parent_skill = load_skill(settings.skills_root, skill, "v001")
    evolution_skills_root = output_path / "skills"

    with ExperimentStore(settings.database_path) as store:
        run_evolution(
            tasks=evolution_tasks,
            fixtures=settings.fixtures_path,
            model=model,
            agent_config=settings.agent,
            experiment_id=experiment_id,
            mode=mode,
            generations=generations,
            runs_per_task=runs_per_task,
            parent_skill=parent_skill,
            skills_root=evolution_skills_root,
            policy=settings.promotion,
            base_seed=settings.seed,
            store=store,
            pricing=settings.pricing,
            config=settings,
        )

    _print_run_summary(output_path, settings.model.model)


@app.command("ablate")
def ablate(
    skill: Annotated[str, typer.Option("--skill")],
    generations: Annotated[int, typer.Option("--generations", min=1, max=20)],
    runs_per_task: Annotated[int, typer.Option("--runs-per-task", min=1)],
    config: Annotated[Path, typer.Option("--config")] = Path("configs/default.json"),
    output: Annotated[Path | None, typer.Option("--output")] = None,
    allow_live: Annotated[bool, typer.Option("--allow-live")] = False,
) -> None:
    """Run verified and naive evolution branches and write both artifacts."""

    settings = _load_cli_config(config)
    output_path, experiment_id, _created_at = _experiment_output(settings.artifacts_root, output)
    _run_ablation_artifact(
        settings=settings,
        skill=skill,
        generations=generations,
        runs_per_task=runs_per_task,
        experiment_id=experiment_id,
        output_path=output_path,
        allow_live=allow_live,
    )
    _print_run_summary(output_path, settings.model.model)


@app.command("report")
def report(
    experiment: Annotated[Path, typer.Option("--experiment")],
) -> None:
    """Print the report stored in an experiment artifact directory."""

    experiment_path = _expanded(experiment)
    report_path = experiment_path / "report.md"
    if not report_path.is_file():
        candidates = sorted(experiment_path.rglob("report.md"))
        if not candidates:
            typer.echo(f"report not found: {experiment_path}", err=True)
            raise typer.Exit(code=1)
        report_path = candidates[0]
    typer.echo(f"report_path: {report_path}")
    typer.echo(report_path.read_text(encoding="utf-8").rstrip("\n"))


@app.command("rerun")
def rerun(
    experiment: Annotated[Path, typer.Option("--experiment")],
    allow_live: Annotated[bool, typer.Option("--allow-live")] = False,
) -> None:
    """Verify an artifact and byte-compare a deterministic regeneration."""

    experiment_path = _expanded(experiment)
    _verify_manifest(experiment_path)
    payload = _read_json(experiment_path / "config.json")
    configuration = payload.get("configuration")
    command = payload.get("command")
    if not isinstance(configuration, Mapping) or not isinstance(command, Mapping):
        typer.echo("artifact config does not contain rerun configuration", err=True)
        raise typer.Exit(code=1)

    try:
        settings = AppConfig.model_validate(configuration)
    except Exception as error:
        typer.echo(f"invalid artifact configuration: {error}", err=True)
        raise typer.Exit(code=1) from error

    if settings.model.provider == "openai_compatible" and not allow_live:
        typer.echo("live rerun requires --allow-live", err=True)
        raise typer.Exit(code=1)

    skill = command.get("skill")
    generations = command.get("generations")
    runs_per_task = command.get("runs_per_task")
    experiment_id = payload.get("experiment_id")
    if (
        not isinstance(skill, str)
        or not isinstance(generations, int)
        or isinstance(generations, bool)
        or not isinstance(runs_per_task, int)
        or isinstance(runs_per_task, bool)
        or not isinstance(experiment_id, str)
    ):
        typer.echo("artifact config has invalid rerun command", err=True)
        raise typer.Exit(code=1)

    try:
        seed_skill = load_skill(experiment_path / "skills", skill, "v001")
    except (OSError, ValueError) as error:
        typer.echo(f"copied seed skill is not readable: {error}", err=True)
        raise typer.Exit(code=1) from error

    with tempfile.TemporaryDirectory(prefix="skill-lab-rerun-") as temporary_root:
        regenerated = Path(temporary_root) / "artifact"
        _run_ablation_artifact(
            settings=settings,
            skill=skill,
            generations=generations,
            runs_per_task=runs_per_task,
            experiment_id=experiment_id,
            output_path=regenerated,
            allow_live=allow_live,
            seed_skill=seed_skill,
        )
        if not _artifact_trees_equal(experiment_path, regenerated):
            typer.echo("rerun output is not byte-identical", err=True)
            raise typer.Exit(code=1)

    typer.echo(f"rerun: byte-identical {experiment_path}")


@app.command("generate-example")
def generate_example(
    config: Annotated[Path, typer.Option("--config")] = Path("configs/default.json"),
) -> None:
    """Generate the fixed offline example artifact."""

    settings = _load_cli_config(config)
    output_path = _expanded(Path("artifacts/example-mock"))
    _run_ablation_artifact(
        settings=settings,
        skill="incident-response",
        generations=2,
        runs_per_task=1,
        experiment_id=_EXAMPLE_EXPERIMENT_ID,
        output_path=output_path,
        allow_live=False,
    )
    _print_run_summary(output_path, settings.model.model)


def _experiment_output(
    artifacts_root: Path,
    output: Path | None,
) -> tuple[Path, str, str]:
    """Create an output directory and a contract-shaped experiment identity."""

    now = datetime.now(UTC)
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    suffix = hashlib.sha256(now.isoformat(timespec="microseconds").encode("ascii")).hexdigest()[:8]
    experiment_id = f"exp-{timestamp}-{suffix}"
    output_path = (
        output.expanduser()
        if output is not None
        else artifacts_root.expanduser() / f"experiment-{timestamp}-{suffix}"
    )
    output_path.mkdir(parents=True, exist_ok=True)
    return output_path, experiment_id, now.isoformat().replace("+00:00", "Z")


def _load_cli_config(path: Path) -> AppConfig:
    """Load a config, retaining the frozen in-code default for the example command."""

    try:
        return load_config(path, os.environ)
    except FileNotFoundError:
        if _expanded(path) == Path("configs/default.json"):
            return default_config()
        raise


def _run_ablation_artifact(
    *,
    settings: AppConfig,
    skill: str,
    generations: int,
    runs_per_task: int,
    experiment_id: str,
    output_path: Path,
    allow_live: bool,
    seed_skill: Skill | None = None,
) -> None:
    """Run one ablation and serialize a stable bundle with both branches."""

    resolved_seed_skill = seed_skill or load_skill(
        settings.skills_root,
        skill,
        "v001",
    )
    tasks = load_tasks(settings.dataset_path)
    output_path.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="skill-lab-ablation-") as temporary_root:
        model = create_chat_model(settings, allow_live=allow_live, env=os.environ)
        try:
            results = run_ablation(
                tasks=tasks,
                fixtures=settings.fixtures_path,
                model=model,
                agent_config=settings.agent,
                experiment_id=experiment_id,
                generations=generations,
                runs_per_task=runs_per_task,
                parent_skill=resolved_seed_skill,
                skills_root=Path(temporary_root) / "skills",
                policy=settings.promotion,
                base_seed=settings.seed,
                pricing=settings.pricing,
                config=settings,
            )
        finally:
            close = getattr(model, "close", None)
            if callable(close):
                close()

    write_artifact(results["verified"], output_path)
    write_artifact(results["naive"], output_path / "naive")
    _write_seed_skill(output_path, resolved_seed_skill)
    _write_bundle_config(
        output_path,
        settings=settings,
        skill=skill,
        generations=generations,
        runs_per_task=runs_per_task,
        experiment_id=experiment_id,
        results=results,
    )
    _write_bundle_manifest(output_path, experiment_id)


def _write_seed_skill(root: Path, skill: Skill) -> None:
    skill_dir = root / "skills" / skill.name / skill.version
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(skill.markdown, encoding="utf-8")
    _write_json(skill_dir / "metadata.json", skill.metadata.model_dump(mode="json"))
    if skill.rationale is not None:
        (skill_dir / "RATIONALE.md").write_text(
            f"{skill.rationale.rstrip(chr(10))}\n",
            encoding="utf-8",
        )


def _write_bundle_config(
    root: Path,
    *,
    settings: AppConfig,
    skill: str,
    generations: int,
    runs_per_task: int,
    experiment_id: str,
    results: Mapping[str, object],
) -> None:
    branches = {
        mode: {
            "experiment_id": result.experiment_id,
            "path": "." if mode == "verified" else mode,
        }
        for mode, result in sorted(results.items())
    }
    _write_json(
        root / "config.json",
        {
            "artifact_kind": "ablation",
            "artifact_schema": _ARTIFACT_SCHEMA,
            "branches": branches,
            "command": {
                "generations": generations,
                "runs_per_task": runs_per_task,
                "skill": skill,
            },
            "configuration": settings.model_dump(mode="json"),
            "experiment_id": experiment_id,
            "mode": "ablation",
        },
    )


def _write_bundle_manifest(root: Path, experiment_id: str) -> None:
    files: dict[str, dict[str, int | str]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == "manifest.json":
            continue
        files[relative] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size": path.stat().st_size,
        }
    _write_json(
        root / "manifest.json",
        {
            "artifact_schema": _ARTIFACT_SCHEMA,
            "experiment_id": experiment_id,
            "files": files,
        },
    )


def _verify_manifest(root: Path) -> None:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        typer.echo(f"manifest not found: {root}", err=True)
        raise typer.Exit(code=1)
    manifest = _read_json(manifest_path)
    entries = manifest.get("files")
    if not isinstance(entries, Mapping):
        typer.echo("manifest has no file entries", err=True)
        raise typer.Exit(code=1)

    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.relative_to(root).as_posix() != "manifest.json"
    }
    expected_paths = {str(path) for path in entries}
    if actual_paths != expected_paths:
        typer.echo("manifest file set does not match artifact", err=True)
        raise typer.Exit(code=1)

    for relative, entry in sorted(entries.items()):
        relative_path = Path(str(relative))
        if relative_path.is_absolute() or ".." in relative_path.parts:
            typer.echo("manifest contains an unsafe path", err=True)
            raise typer.Exit(code=1)
        path = root / relative_path
        if not isinstance(entry, Mapping):
            typer.echo(f"manifest entry is invalid: {relative}", err=True)
            raise typer.Exit(code=1)
        expected_hash = entry.get("sha256")
        expected_size = entry.get("size")
        if not isinstance(expected_hash, str) or not isinstance(expected_size, int):
            typer.echo(f"manifest entry is invalid: {relative}", err=True)
            raise typer.Exit(code=1)
        if (
            hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash
            or path.stat().st_size != expected_size
        ):
            typer.echo(f"manifest hash mismatch: {relative}", err=True)
            raise typer.Exit(code=1)


def _artifact_trees_equal(first: Path, second: Path) -> bool:
    first_files = {
        path.relative_to(first).as_posix(): path.read_bytes()
        for path in first.rglob("*")
        if path.is_file()
    }
    second_files = {
        path.relative_to(second).as_posix(): path.read_bytes()
        for path in second.rglob("*")
        if path.is_file()
    }
    return first_files == second_files


def _read_json(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _expanded(path: Path | str) -> Path:
    return Path(os.path.expanduser(os.fspath(path)))


def _print_run_summary(output_path: Path, model_id: str) -> None:
    """Print only the non-secret identifiers needed to locate a run."""

    typer.echo(f"artifact_path: {output_path}")
    typer.echo(f"model_id: {model_id}")
