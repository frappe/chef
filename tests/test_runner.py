"""``run_phase`` against the ``@local`` connector — an echo op is safe to actually run.

Hermetic: no network, no apt. Drives the real pyinfra 3.x path end to end and asserts the
event contract (``step`` + ``line``), plus the empty-phase no-op and the fail-loud raise.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chef.engine.pyinfra_runner import RunPhaseError, run_phase
from chef.engine.recipe import load_recipe
from chef.types import SshTarget

RECIPES_DIR = Path(__file__).resolve().parent.parent / "recipes"

LOCAL_TARGET = SshTarget(connector="local", host="@local")


def _collect(target, recipe, phase, inputs):
    events: list[dict] = []
    run_phase(target, recipe, phase, inputs, events.append)
    return events


def test_run_hello_build_emits_step_and_line_events():
    recipe = load_recipe(RECIPES_DIR, "hello")
    events = _collect(LOCAL_TARGET, recipe, "build", {})

    steps = [e for e in events if e["type"] == "step"]
    lines = [e for e in events if e["type"] in ("line", "overwrite")]

    # The hello build is a single op, so exactly one step, indexed 1/1.
    assert len(steps) == 1
    step = steps[0]
    assert step["index"] == 1
    assert step["total"] == 1
    assert step["state"] in ("changed", "no_change")
    assert "say hello" in step["name"]

    # The echo op's stdout must have surfaced as a line event.
    assert any("baking hello" in e["line"] for e in lines)


def test_empty_phase_is_a_noop():
    recipe = load_recipe(RECIPES_DIR, "hello")
    # hello has no verify phase -> load_phase returns None -> nothing emitted.
    events = _collect(LOCAL_TARGET, recipe, "verify", {})
    assert events == []


def test_failed_op_raises_and_emits_failed_step(tmp_path):
    _write_boom_recipe(tmp_path)
    recipe = load_recipe(tmp_path, "boom")

    events: list[dict] = []
    with pytest.raises(RunPhaseError):
        run_phase(LOCAL_TARGET, recipe, "build", {}, events.append)

    failed = [e for e in events if e["type"] == "step" and e["state"] == "failed"]
    assert failed, "a failed op must emit a failed step before raising"


def _write_boom_recipe(root: Path) -> None:
    recipe_dir = root / "boom"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.toml").write_text(
        'name = "boom"\n'
        'version = "1.0.0"\n'
        'base_image = "ubuntu-24.04"\n'
        "[phases]\n"
        'build = "recipe:build"\n'
    )
    (recipe_dir / "recipe.py").write_text(
        "from pyinfra.api import deploy\n"
        "from pyinfra.operations import server\n\n\n"
        '@deploy("build")\n'
        "def build():\n"
        '    server.shell(name="explode", commands=["exit 7"])\n'
    )
