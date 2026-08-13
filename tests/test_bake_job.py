"""End-to-end (hermetic) test of the bake pipeline via ``run_bake_inline``.

No Docker, no Redis, no fleet: a fake Builder + fake Publisher are monkeypatched into the
worker, ``run_phase`` is stubbed to emit a couple of step events, and the recipe + SQLite
DB live under ``tmp_path``. Asserts the bake reaches ``succeeded``, an ImageRecord is
written, steps are persisted, the builder is released, and the event stream ends in
``done{exit_code:0}``."""

from __future__ import annotations

from pathlib import Path

import pytest

from chef import store
from chef.builders.base import Builder
from chef.events import line_event, step_event
from chef.publishers.base import Publisher
from chef.types import (
    BuildSize,
    ImageLocation,
    SnapshotKind,
    SnapshotRef,
    SshTarget,
    StepState,
)
from chef.worker import bake_job

HELLO_TOML = """\
name        = "hello"
version     = "1.0.0"
description = "trivial"
base_image  = "ubuntu-24.04"
modes       = ["cold"]

[phases]
build = "recipe:build"

[[publish]]
type = "local"
"""

HELLO_PY = """\
from pyinfra.api import deploy
from pyinfra.operations import server


@deploy("build")
def build():
    server.shell(name="hello", commands=["true"])
"""


@pytest.fixture
def chef_env(tmp_path: Path, monkeypatch):
    """A temp recipes dir (with a ``hello`` recipe) + temp SQLite DB, wired into config."""
    recipes = tmp_path / "recipes"
    (recipes / "hello").mkdir(parents=True)
    (recipes / "hello" / "recipe.toml").write_text(HELLO_TOML)
    (recipes / "hello" / "recipe.py").write_text(HELLO_PY)

    monkeypatch.setenv("CHEF_RECIPES_DIR", str(recipes))
    monkeypatch.setenv("CHEF_DATABASE_URL", f"sqlite:///{tmp_path / 'chef.db'}")

    from chef.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(store, "_engine", None)  # rebuild the engine against the temp DB
    store.init_db()
    yield
    get_settings.cache_clear()


class FakeBuilder(Builder):
    name = "fake"

    def __init__(self):
        self.released = False
        self.stopped = False

    def acquire(self, base_image, size, *, title):
        assert isinstance(size, BuildSize)
        return SshTarget(connector="local", host="@fake", vm_ref="fake-vm-1")

    def stop(self, target):
        self.stopped = True

    def snapshot(self, target, kind, *, title):
        return SnapshotRef(kind=SnapshotKind(kind), ref=f"/fake/{kind}.tar", size_bytes=42)

    def release(self, target):
        self.released = True


class FakePublisher(Publisher):
    type = "local"

    def __init__(self):
        self.calls = []

    def publish(self, snapshot, *, recipe, version, config):
        self.calls.append((recipe, version, snapshot.kind.value))
        return ImageLocation(
            type="local",
            uri=f"file:///images/{recipe}-{snapshot.kind.value}.tar",
            manifest={"kind": snapshot.kind.value},
        )


def _fake_run_phase(target, recipe, phase, inputs, emit, releases=None):
    emit(step_event(f"{phase}:op-a", 0, 2, StepState.changed))
    emit(step_event(f"{phase}:op-b", 1, 2, StepState.no_change))
    emit(line_event(f"ran {phase}"))


def test_bake_inline_succeeds(chef_env, monkeypatch):
    fake_builder = FakeBuilder()
    fake_publisher = FakePublisher()

    monkeypatch.setattr(bake_job, "get_builder", lambda name: fake_builder)
    monkeypatch.setattr(bake_job, "get_publisher", lambda type: fake_publisher)
    monkeypatch.setattr(bake_job, "run_phase", _fake_run_phase)

    store.create_bake(
        store.BakeRecord(id="bake-1", recipe="hello", mode="cold", builder="fake", inputs={})
    )

    events = bake_job.run_bake_inline("bake-1")

    # bake reached the success terminal state
    bake = store.get_bake("bake-1")
    assert bake.status == "succeeded"
    assert bake.exit_code == 0
    assert bake.vm_ref == "fake-vm-1"

    # the builder was released and stopped for the cold capture
    assert fake_builder.released is True
    assert fake_builder.stopped is True

    # exactly one image (cold × one local publish block)
    images = store.images_for_bake("bake-1")
    assert len(images) == 1
    img = images[0]
    assert img.recipe == "hello"
    assert img.version == "1.0.0"
    assert img.kind == "cold"
    assert img.location_type == "local"
    assert img.location_uri.startswith("file://")
    assert fake_publisher.calls == [("hello", "1.0.0", "cold")]

    # steps from the build phase were mirrored into the store
    steps = store.list_steps("bake-1")
    assert len(steps) == 2
    assert {s.state for s in steps} == {"changed", "no_change"}
    assert all(s.phase == "build" for s in steps)

    # the event stream ends in a terminal success
    done = [e for e in events if e["type"] == "done"]
    assert done and done[-1]["exit_code"] == 0
    assert done[-1]["status"] == "succeeded"
    # and carried the status transitions
    statuses = [e["status"] for e in events if e["type"] == "status"]
    assert "acquiring" in statuses
    assert "building" in statuses
    assert "publishing" in statuses


def test_bake_inline_records_failure(chef_env, monkeypatch):
    fake_builder = FakeBuilder()

    monkeypatch.setattr(bake_job, "get_builder", lambda name: fake_builder)

    def _boom(target, recipe, phase, inputs, emit, releases=None):
        raise RuntimeError("build blew up")

    monkeypatch.setattr(bake_job, "run_phase", _boom)

    store.create_bake(
        store.BakeRecord(id="bake-2", recipe="hello", mode="cold", builder="fake", inputs={})
    )

    events = bake_job.run_bake_inline("bake-2")

    bake = store.get_bake("bake-2")
    assert bake.status == "failed"
    assert bake.exit_code == 1
    assert "build blew up" in (bake.error or "")

    # builder still released on the failure path
    assert fake_builder.released is True

    done = [e for e in events if e["type"] == "done"]
    assert done and done[-1]["exit_code"] == 1
    assert done[-1]["status"] == "failed"
