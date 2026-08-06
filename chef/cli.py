"""``chef`` — the command-line entry point (Typer).

Commands:
  * ``serve``           run the API (uvicorn).
  * ``worker``          run the arq bake worker.
  * ``bake``            run a recipe inline (no redis) and print streamed events.
  * ``new``             scaffold a recipe directory from the template.
  * ``install-service`` (M5) install a systemd unit — stub for now.

Heavy / optional modules (uvicorn, arq, the worker package) are imported lazily inside
each command so ``import chef.cli`` always succeeds even before the worker lands.
"""

from __future__ import annotations

import json
from typing import Optional
from uuid import uuid4

import typer

from chef.config import get_settings

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Chef — a declarative, agent-friendly image-baking service.",
)


def _parse_inputs(pairs: Optional[list[str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise typer.BadParameter(f"expected key=value, got {pair!r}", param_hint="--input")
        key, _, value = pair.partition("=")
        out[key.strip()] = value
    return out


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind address."),
    port: int = typer.Option(8000, help="Bind port."),
    reload: bool = typer.Option(False, help="Auto-reload on code changes (dev)."),
) -> None:
    """Run the Chef API server."""
    import uvicorn

    uvicorn.run("chef.app.main:app", host=host, port=port, reload=reload)


@app.command()
def worker() -> None:
    """Run the arq worker that executes bakes."""
    from arq import run_worker

    from chef.worker.settings import WorkerSettings

    run_worker(WorkerSettings)


def _print_event(event: dict) -> None:
    """Render one bake event dict to the console (mirrors the SSE wire shapes)."""
    etype = event.get("type")
    if etype == "line":
        typer.echo(event.get("line", ""))
    elif etype == "overwrite":
        typer.echo("\r" + event.get("line", ""), nl=False)
    elif etype == "step":
        typer.echo(
            f"  [{event.get('index')}/{event.get('total')}] "
            f"{event.get('name')} — {event.get('state')}"
        )
    elif etype == "status":
        phase = event.get("phase")
        typer.secho(f"== {event.get('status')}{f' ({phase})' if phase else ''}", fg="cyan")
    elif etype == "done":
        code = event.get("exit_code", 1)
        typer.secho(
            f"== done: {event.get('status')} (exit {code})",
            fg="green" if code == 0 else "red",
        )
    else:
        typer.echo(json.dumps(event, separators=(",", ":")))


@app.command()
def bake(
    recipe: str = typer.Argument(..., help="Recipe name (a directory under recipes/)."),
    input: Optional[list[str]] = typer.Option(
        None, "--input", "-i", help="Recipe input as key=value (repeatable)."
    ),
    mode: str = typer.Option("cold", help="cold | warm | both."),
    builder: Optional[str] = typer.Option(None, help="Override the default builder."),
) -> None:
    """Run a recipe inline (no redis/worker) and print its events to the console.

    Creates the bake row, runs the *same* pipeline synchronously via
    ``run_bake_inline``, then prints the collected events. Exit code mirrors the
    terminal ``done`` event.
    """
    from chef.engine.recipe import RecipeError, load_recipe
    from chef.store import BakeRecord, create_bake, init_db

    try:
        from chef.worker.bake_job import run_bake_inline
    except Exception as exc:  # noqa: BLE001 - worker not present yet
        typer.secho(f"inline bake unavailable: {exc}", fg="red", err=True)
        raise typer.Exit(1) from exc

    settings = get_settings()
    try:
        rcp = load_recipe(settings.recipes_dir, recipe)
        resolved = rcp.validate_inputs(_parse_inputs(input))
    except RecipeError as exc:
        typer.secho(f"recipe error: {exc}", fg="red", err=True)
        raise typer.Exit(2) from exc

    init_db()
    bake_id = str(uuid4())
    chosen_builder = builder or settings.default_builder
    create_bake(
        BakeRecord(
            id=bake_id,
            recipe=recipe,
            version=rcp.manifest.version,
            mode=mode,
            builder=chosen_builder,
            inputs=resolved,
            status="queued",
        )
    )
    typer.secho(f"baking {recipe} (bake {bake_id}, builder={chosen_builder})", fg="cyan")

    exit_code = 0
    for event in run_bake_inline(bake_id):
        _print_event(event)
        if event.get("type") == "done":
            exit_code = int(event.get("exit_code", 0) or 0)
    raise typer.Exit(exit_code)


@app.command()
def new(name: str = typer.Argument(..., help="New recipe name.")) -> None:
    """Scaffold a new recipe directory from the template."""
    from chef.engine.recipe import recipe_template

    dest = get_settings().recipes_dir / name
    if dest.exists():
        typer.secho(f"{dest} already exists", fg="red", err=True)
        raise typer.Exit(1)
    dest.mkdir(parents=True)
    for filename, content in recipe_template().items():
        (dest / filename).write_text(content)
    typer.secho(f"created recipe {name} at {dest}", fg="green")


@app.command(name="install-service")
def install_service() -> None:
    """Install Chef as a systemd service (M5)."""
    typer.echo("not yet — systemd install lands in M5")


def main() -> None:
    """Console-script entry point (``chef``)."""
    app()


if __name__ == "__main__":
    main()
