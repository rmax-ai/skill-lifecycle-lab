"""Command-line entry point for the skill lifecycle lab."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

from skill_lab.config import load_config
from skill_lab.data_validation import validate_operator_inputs

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
