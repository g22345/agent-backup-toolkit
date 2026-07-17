"""Public command-line interface."""

from __future__ import annotations

import importlib.util
import os
import shutil
import stat
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from agent_backup_toolkit import __version__
from agent_backup_toolkit.config import (
    GitHubDestination,
    S3Destination,
    default_config_path,
    load_config,
    write_starter_config,
)
from agent_backup_toolkit.errors import ConfigError, ToolkitError
from agent_backup_toolkit.orchestrator import destination_from_config, run_backup
from agent_backup_toolkit.state import latest_success_receipt

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Create encrypted, fail-closed backups of allowlisted AI-agent workspace data.",
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"agent-backup-toolkit {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the installed version and exit.",
    ),
) -> None:
    """Create encrypted, fail-closed backups of allowlisted data."""


def _fail(error: ToolkitError) -> NoReturn:
    typer.secho(f"Error: {error}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=int(error.exit_code))


def _config_permissions_are_safe(path: Path) -> bool:
    if os.name == "nt":
        return True
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return False
    return not bool(mode & (stat.S_IWGRP | stat.S_IWOTH))


@app.command("init")
def init_command(
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Path for the new configuration."),
    ] = None,
) -> None:
    """Create a conservative starter configuration without overwriting files."""

    target = (config or default_config_path()).expanduser()
    try:
        write_starter_config(target)
    except ToolkitError as exc:
        _fail(exc)
    typer.echo(f"Created configuration: {target}")
    typer.echo("Next: add your public age recipient, then run 'agent-backup doctor'.")


@app.command()
def doctor(
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Configuration to validate."),
    ] = None,
) -> None:
    """Check configuration and local dependencies without uploading anything."""

    target = (config or default_config_path()).expanduser()
    try:
        parsed = load_config(target)
        if not _config_permissions_are_safe(target):
            raise ConfigError("Configuration permissions allow unsafe group/world writes.")
        if shutil.which("age") is None:
            raise ConfigError("Required dependency 'age' was not found on PATH.")
        if isinstance(parsed.destination, GitHubDestination) and shutil.which("gh") is None:
            raise ConfigError("GitHub destination requires the 'gh' command on PATH.")
        if (
            isinstance(parsed.destination, S3Destination)
            and importlib.util.find_spec("boto3") is None
        ):
            raise ConfigError("S3 destination requires the optional 'boto3' dependency.")
        destination_from_config(parsed).preflight()
    except ToolkitError as exc:
        _fail(exc)
    typer.echo("Doctor checks passed. No backup data was uploaded.")


@app.command()
def status(
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Configuration to inspect."),
    ] = None,
) -> None:
    """Show only the latest fully verified local success state."""

    target = (config or default_config_path()).expanduser()
    try:
        parsed = load_config(target)
        receipt = latest_success_receipt(parsed.state_dir)
    except ToolkitError as exc:
        _fail(exc)
    if receipt is None:
        typer.echo("No fully verified backup has been recorded.")
        return
    backup_id = receipt.get("backup_id", "unknown")
    completed_at = receipt.get("completed_at", "unknown")
    typer.echo(f"Latest verified backup: {backup_id}")
    typer.echo(f"Completed: {completed_at}")


def _foundation_only(command: str) -> NoReturn:
    _fail(ConfigError(f"'{command}' is not available until the backup workflow is installed."))


@app.command()
def backup(
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Configuration to use."),
    ] = None,
) -> None:
    """Create and verify a new encrypted backup."""

    target = (config or default_config_path()).expanduser()
    try:
        parsed = load_config(target)
        receipt = run_backup(parsed)
    except ToolkitError as exc:
        _fail(exc)
    typer.echo(f"Backup verified: {receipt.backup_id}")


@app.command()
def verify() -> None:
    """Verify an encrypted backup without restoring it."""

    _foundation_only("verify")


@app.command()
def restore() -> None:
    """Preview or apply a verified restore."""

    _foundation_only("restore")


def run() -> None:
    """Console-script entrypoint."""

    app()


if __name__ == "__main__":
    run()
