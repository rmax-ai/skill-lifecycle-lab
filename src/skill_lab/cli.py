"""Command-line entry point for the skill lifecycle lab."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

import typer

from skill_lab.config import create_chat_model, load_config
from skill_lab.data_validation import validate_operator_inputs
from skill_lab.experiment import evaluate_condition
from skill_lab.experiment import evolve as run_evolution
from skill_lab.skills import load_skill
from skill_lab.storage import ExperimentStore
from skill_lab.tasks import load_tasks

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def main() -> None:
    """Run skill lifecycle lab commands."""


@app.command("validate-data")
def validate_data(
    config: Annotated[Path, typer.Option("--config")] = Path("configs/default.json"),
) -> None:
    """Validate the configured dataset, fixtures, and seed skill."""

    settings = load_config(config, os.environ)
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

    settings = load_config(config, os.environ)
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

    settings = load_config(config, os.environ)
    output_path, experiment_id, _created_at = _experiment_output(settings.artifacts_root, output)
    model = create_chat_model(settings, allow_live=allow_live, env=os.environ)
    tasks = load_tasks(settings.dataset_path)
    parent_skill = load_skill(settings.skills_root, skill, "v001")
    evolution_skills_root = output_path / "skills"

    with ExperimentStore(settings.database_path) as store:
        run_evolution(
            tasks=tasks,
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


def _print_run_summary(output_path: Path, model_id: str) -> None:
    """Print only the non-secret identifiers needed to locate a run."""

    typer.echo(f"artifact_path: {output_path}")
    typer.echo(f"model_id: {model_id}")
