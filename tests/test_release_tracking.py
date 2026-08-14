"""Release tracking end-to-end through the recipe engine and the bake pipeline.

Covers: ``[[track]]`` parsing, composition union of tracked repos, the pipeline's
fail-closed behaviour when a tracked repo is unpinned, store-pin vs per-bake-override
precedence, and that the resolved ``{ref, sha}`` lands in image provenance. Hermetic —
a fake Builder/Publisher, a stubbed ``run_phase``, and a stubbed ``resolve_ref`` (no git).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from chef import store
from chef.builders.base import Builder
from chef.engine.recipe import load_recipe
from chef.types import BuildSize, ImageLocation, SnapshotKind, SnapshotRef, SshTarget
from chef.worker import bake_job

_BUILD_PY = """\
from pyinfra.api import deploy
from pyinfra.operations import server


@deploy("build")
def build():
    server.shell(name="hello", commands=["true"])
"""


def _recipe(recipes: Path, name: str, toml: str) -> None:
    d = recipes / name
    d.mkdir(parents=True)
    (d / "recipe.toml").write_text(toml)
    (d / "recipe.py").write_text(_BUILD_PY)


@pytest.fixture
def tracked_env(tmp_path: Path, monkeypatch):
    """Temp recipes (a tracked leaf + a compose of it) + temp DB, wired into config."""
    recipes = tmp_path / "recipes"
    _recipe(recipes, "wid", (
        'name="wid"\nversion="1.0.0"\nbase_image="ubuntu-24.04"\nmodes=["cold"]\n'
        '[phases]\nbuild="recipe:build"\n'
        '[[track]]\nrepo="acme/widget"\n'
        '[[publish]]\ntype="local"\n'
    ))
    _recipe(recipes, "combo", (
        'name="combo"\nversion="1.0.0"\ncompose=["wid"]\n'
        '[phases]\nbuild="recipe:build"\n'
        '[[track]]\nrepo="acme/gadget"\n'
        '[[publish]]\ntype="local"\n'
    ))
    _recipe(recipes, "dup", (
        'name="dup"\nversion="1.0.0"\ncompose=["wid"]\n'
        '[phases]\nbuild="recipe:build"\n'
        '[[track]]\nrepo="acme/widget"\n'  # same repo the base tracks → dedup
        '[[publish]]\ntype="local"\n'
    ))

    monkeypatch.setenv("CHEF_RECIPES_DIR", str(recipes))
    monkeypatch.setenv("CHEF_DATABASE_URL", f"sqlite:///{tmp_path / 'chef.db'}")
    from chef.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(store, "_engine", None)
    store.init_db()
    yield recipes
    get_settings.cache_clear()


# --- manifest parsing + composition union ------------------------------------


def test_track_parsed(tracked_env):
    r = load_recipe(tracked_env, "wid")
    assert r.manifest.track == [{"repo": "acme/widget"}]
    assert r.tracked_repos() == ["acme/widget"]


def test_track_union_via_compose(tracked_env):
    r = load_recipe(tracked_env, "combo")
    # base-first, then own; both repos present
    assert r.tracked_repos() == ["acme/widget", "acme/gadget"]


def test_track_union_dedups(tracked_env):
    r = load_recipe(tracked_env, "dup")
    assert r.tracked_repos() == ["acme/widget"]


def test_track_missing_repo_is_a_recipe_error(tmp_path):
    from chef.engine.recipe import RecipeError

    d = tmp_path / "bad"
    d.mkdir()
    (d / "recipe.toml").write_text(
        'name="bad"\nversion="1.0.0"\nbase_image="ubuntu-24.04"\n'
        '[phases]\nbuild="recipe:build"\n[[track]]\nname="oops"\n'
    )
    (d / "recipe.py").write_text(_BUILD_PY)
    with pytest.raises(RecipeError):
        load_recipe(tmp_path, "bad")


# --- pipeline: fail-closed, precedence, provenance ---------------------------


class _FakeBuilder(Builder):
    name = "fake"

    def __init__(self):
        self.acquired = False
        self.released = False

    def acquire(self, base_image, size, *, title):
        assert isinstance(size, BuildSize)
        self.acquired = True
        return SshTarget(connector="local", host="@fake", vm_ref="vm-1")

    def stop(self, target):
        pass

    def snapshot(self, target, kind, *, title):
        return SnapshotRef(kind=SnapshotKind(kind), ref=f"/fake/{kind}.tar", size_bytes=1)

    def release(self, target):
        self.released = True


class _FakePublisher:
    type = "local"
    builders: tuple = ()
    kinds: tuple = ()  # falsy → consumes any snapshot kind (like builders)

    def publish(self, snapshot, *, recipe, version, config):
        return ImageLocation(type="local", uri=f"file:///img/{recipe}.tar", manifest={})


def _wire(monkeypatch, builder=None, captured=None):
    builder = builder or _FakeBuilder()
    monkeypatch.setattr(bake_job, "get_builder", lambda name: builder)
    monkeypatch.setattr(bake_job, "get_publisher", lambda t: _FakePublisher())

    def _run_phase(target, recipe, phase, inputs, emit, releases=None):
        if captured is not None:
            captured["releases"] = releases

    monkeypatch.setattr(bake_job, "run_phase", _run_phase)
    return builder


def test_bake_fails_closed_when_unpinned(tracked_env, monkeypatch):
    builder = _wire(monkeypatch)
    store.create_bake(store.BakeRecord(id="b1", recipe="wid", mode="cold", builder="fake"))

    bake_job.run_bake_inline("b1")

    bake = store.get_bake("b1")
    assert bake.status == "failed"
    assert "no release is pinned" in (bake.error or "")
    assert builder.acquired is False  # fail-closed before acquiring a VM


def test_bake_uses_store_pin_and_records_provenance(tracked_env, monkeypatch):
    captured: dict = {}
    _wire(monkeypatch, captured=captured)
    monkeypatch.setattr(bake_job, "resolve_ref", lambda repo, ref: "sha-" + ref)
    store.set_pin("acme/widget", "v1", "sha-v1")

    store.create_bake(store.BakeRecord(id="b2", recipe="wid", mode="cold", builder="fake"))
    bake_job.run_bake_inline("b2")

    bake = store.get_bake("b2")
    assert bake.status == "succeeded", bake.error
    # injected into the phase as host data
    assert captured["releases"] == {"acme/widget": {"ref": "v1", "sha": "sha-v1"}}
    # and recorded in the image's provenance
    img = store.images_for_bake("b2")[0]
    assert img.provenance["releases"] == {"acme/widget": {"ref": "v1", "sha": "sha-v1"}}


def test_per_bake_override_beats_store_pin(tracked_env, monkeypatch):
    captured: dict = {}
    _wire(monkeypatch, captured=captured)
    monkeypatch.setattr(bake_job, "resolve_ref", lambda repo, ref: "sha-" + ref)
    store.set_pin("acme/widget", "v1", "sha-v1")

    store.create_bake(store.BakeRecord(
        id="b3", recipe="wid", mode="cold", builder="fake", releases={"acme/widget": "v2"}
    ))
    bake_job.run_bake_inline("b3")

    img = store.images_for_bake("b3")[0]
    assert img.provenance["releases"] == {"acme/widget": {"ref": "v2", "sha": "sha-v2"}}


def test_bake_fails_when_ref_unresolvable(tracked_env, monkeypatch):
    _wire(monkeypatch)
    monkeypatch.setattr(bake_job, "resolve_ref", lambda repo, ref: None)  # not found
    store.set_pin("acme/widget", "v-bogus", "")

    store.create_bake(store.BakeRecord(id="b4", recipe="wid", mode="cold", builder="fake"))
    bake_job.run_bake_inline("b4")

    bake = store.get_bake("b4")
    assert bake.status == "failed"
    assert "not found" in (bake.error or "")


# --- pilot recipe: declares the repo + builds the pinned tarball URL ----------


def _load_pilot_module():
    path = Path("recipes/pilot/recipe.py")
    spec = importlib.util.spec_from_file_location("pilot_recipe_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_pilot_declares_track():
    r = load_recipe("recipes", "pilot")
    assert r.tracked_repos() == ["frappe/pilot"]


def test_pilot_pinned_install_url():
    mod = _load_pilot_module()
    script = mod._pinned_install_script("v0.0.23-pre-alpha")
    assert (
        "https://github.com/frappe/pilot/releases/download/v0.0.23-pre-alpha/pilot.tar.gz"
        in script
    )
