"""Recipe composition — `compose = [...]` linearization, the merge algebra, and the
resolved phase chain. Built on synthetic recipes under ``tmp_path`` so the assertions are
deterministic and independent of the shipped recipes."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from chef.engine.recipe import RecipeError, load_recipe

_BUILD_PY = """\
from pyinfra.api import deploy
from pyinfra.operations import server


@deploy("build")
def build():
    server.shell(name="{n} build", commands=["true"])


@deploy("verify")
def verify():
    server.shell(name="{n} verify", commands=["true"])
"""


def _mk(root: Path, name: str, toml: str, *, with_py: bool = False) -> None:
    d = root / name
    d.mkdir()
    (d / "recipe.toml").write_text(textwrap.dedent(toml))
    if with_py:
        (d / "recipe.py").write_text(_BUILD_PY.format(n=name))


def _leaf(name: str, **kw) -> str:
    """A plain single recipe with build+verify phases."""
    extra = "".join(f"{k} = {v}\n" for k, v in kw.items())
    return f"""\
        name = "{name}"
        version = "1"
        base_image = "ubuntu-24.04"
        {extra}[phases]
        build = "recipe:build"
        verify = "recipe:verify"
    """


@pytest.fixture
def recipes(tmp_path: Path) -> Path:
    root = tmp_path / "recipes"
    root.mkdir()
    return root


# --- linearization -----------------------------------------------------------


def test_lineage_is_bases_first_then_self(recipes: Path):
    _mk(recipes, "a", _leaf("a"), with_py=True)
    _mk(recipes, "b", _leaf("b"), with_py=True)
    _mk(recipes, "c", 'name="c"\nversion="1"\ncompose=["a","b"]\n', with_py=False)
    assert load_recipe(recipes, "c").lineage == ["a", "b", "c"]


def test_diamond_dedupes_first_occurrence_wins(recipes: Path):
    # F -> (D -> A), (E -> A): A appears via two paths but is stacked once, before D and E.
    _mk(recipes, "a", _leaf("a"), with_py=True)
    _mk(recipes, "d", 'name="d"\nversion="1"\ncompose=["a"]\n')
    _mk(recipes, "e", 'name="e"\nversion="1"\ncompose=["a"]\n')
    _mk(recipes, "f", 'name="f"\nversion="1"\ncompose=["d","e"]\n')
    assert load_recipe(recipes, "f").lineage == ["a", "d", "e", "f"]


def test_self_phase_runs_last(recipes: Path):
    _mk(recipes, "a", _leaf("a"), with_py=True)
    _mk(recipes, "b", _leaf("b"), with_py=True)
    _mk(recipes, "c", _leaf("c", compose='["a","b"]'), with_py=True)
    r = load_recipe(recipes, "c")
    assert [n for n, _ in r.phase_chain("build")] == ["a", "b", "c"]
    assert [n for n, _ in r.phase_chain("verify")] == ["a", "b", "c"]


def test_pure_composition_has_no_own_phase(recipes: Path):
    _mk(recipes, "a", _leaf("a"), with_py=True)
    _mk(recipes, "b", _leaf("b"), with_py=True)
    _mk(recipes, "c", 'name="c"\nversion="1"\ncompose=["a","b"]\n')  # no [phases], no recipe.py
    r = load_recipe(recipes, "c")
    assert [n for n, _ in r.phase_chain("build")] == ["a", "b"]
    assert r.has_phase("build") is True


# --- the merge algebra -------------------------------------------------------


def test_size_is_per_field_max_over_declared(recipes: Path):
    _mk(recipes, "a", _leaf("a") + "[size]\nvcpus=1\nmemory_megabytes=512\ndisk_gigabytes=10\n",
        with_py=True)
    _mk(recipes, "b", _leaf("b") + "[size]\nvcpus=4\nmemory_megabytes=1024\ndisk_gigabytes=8\n",
        with_py=True)
    # c declares no [size] -> its default must NOT inflate the max; inherits max(a, b).
    _mk(recipes, "c", 'name="c"\nversion="1"\ncompose=["a","b"]\n')
    s = load_recipe(recipes, "c").manifest.size
    assert (s.vcpus, s.memory_megabytes, s.disk_gigabytes) == (4, 1024, 10)


def test_declared_size_participates_in_the_max(recipes: Path):
    _mk(recipes, "a", _leaf("a") + "[size]\nvcpus=1\nmemory_megabytes=512\ndisk_gigabytes=10\n",
        with_py=True)
    _mk(recipes, "c",
        'name="c"\nversion="1"\ncompose=["a"]\n[size]\nvcpus=8\nmemory_megabytes=256\ndisk_gigabytes=5\n')
    s = load_recipe(recipes, "c").manifest.size
    assert (s.vcpus, s.memory_megabytes, s.disk_gigabytes) == (8, 512, 10)


def test_inputs_union_later_in_stack_wins(recipes: Path):
    _mk(recipes, "a",
        _leaf("a") + '[inputs.shared]\ntype="string"\ndefault="from-a"\n'
        '[inputs.only_a]\ntype="string"\ndefault="a"\n', with_py=True)
    _mk(recipes, "b",
        _leaf("b") + '[inputs.shared]\ntype="string"\ndefault="from-b"\n', with_py=True)
    _mk(recipes, "c",
        'name="c"\nversion="1"\ncompose=["a","b"]\n'
        '[inputs.shared]\ntype="string"\ndefault="from-c"\n')
    m = load_recipe(recipes, "c").manifest
    assert set(m.inputs) == {"shared", "only_a"}
    assert m.inputs["shared"]["default"] == "from-c"          # self overrides both bases
    # and validate_inputs resolves the union with the override applied.
    resolved = load_recipe(recipes, "c").validate_inputs(None)
    assert resolved == {"shared": "from-c", "only_a": "a"}


def test_modes_intersect_bases_but_explicit_wins(recipes: Path):
    _mk(recipes, "a", _leaf("a", modes='["cold","warm"]'), with_py=True)
    _mk(recipes, "b", _leaf("b", modes='["cold"]'), with_py=True)
    # undeclared -> intersection of bases (cold only)
    _mk(recipes, "c", 'name="c"\nversion="1"\ncompose=["a","b"]\n')
    assert load_recipe(recipes, "c").manifest.modes == ["cold"]
    # explicitly declared -> author's word wins (widen back to warm)
    _mk(recipes, "d", 'name="d"\nversion="1"\ncompose=["a","b"]\nmodes=["cold","warm"]\n')
    assert load_recipe(recipes, "d").manifest.modes == ["cold", "warm"]


def test_tags_union_plus_composed_marker(recipes: Path):
    _mk(recipes, "a", _leaf("a", tags='["x"]'), with_py=True)
    _mk(recipes, "b", _leaf("b", tags='["y"]'), with_py=True)
    _mk(recipes, "c", 'name="c"\nversion="1"\ncompose=["a","b"]\ntags=["z"]\n')
    assert load_recipe(recipes, "c").manifest.tags == ["x", "y", "z", "composed"]


def test_publish_is_own_only(recipes: Path):
    _mk(recipes, "a", _leaf("a") + '[[publish]]\ntype="atlas-base-image"\nname="a-img"\n',
        with_py=True)
    _mk(recipes, "c", 'name="c"\nversion="1"\ncompose=["a"]\n[[publish]]\ntype="local"\n')
    assert load_recipe(recipes, "c").manifest.publish == [{"type": "local"}]


def test_base_image_agrees_or_errors(recipes: Path):
    _mk(recipes, "a", _leaf("a"), with_py=True)  # ubuntu-24.04
    _mk(recipes, "b", 'name="b"\nversion="1"\nbase_image="debian-12"\n'
        '[phases]\nbuild="recipe:build"\n', with_py=True)
    _mk(recipes, "clash", 'name="clash"\nversion="1"\ncompose=["a","b"]\n')
    with pytest.raises(RecipeError, match="base_image"):
        load_recipe(recipes, "clash")
    # setting it explicitly resolves the clash
    _mk(recipes, "fixed",
        'name="fixed"\nversion="1"\ncompose=["a","b"]\nbase_image="ubuntu-24.04"\n')
    assert load_recipe(recipes, "fixed").manifest.base_image == "ubuntu-24.04"


# --- source + errors ---------------------------------------------------------


def test_source_unions_stack_with_prefixes(recipes: Path):
    _mk(recipes, "a", _leaf("a"), with_py=True)
    _mk(recipes, "c", _leaf("c", compose='["a"]'), with_py=True)
    keys = set(load_recipe(recipes, "c").source())
    assert {"a/recipe.toml", "a/recipe.py", "c/recipe.toml", "c/recipe.py"} <= keys


def test_cycle_is_rejected(recipes: Path):
    _mk(recipes, "x", 'name="x"\nversion="1"\ncompose=["y"]\n')
    _mk(recipes, "y", 'name="y"\nversion="1"\ncompose=["x"]\n')
    with pytest.raises(RecipeError, match="cycle"):
        load_recipe(recipes, "x")


def test_missing_base_is_rejected(recipes: Path):
    _mk(recipes, "bad", 'name="bad"\nversion="1"\ncompose=["nope"]\n')
    with pytest.raises(RecipeError, match="not found"):
        load_recipe(recipes, "bad")
