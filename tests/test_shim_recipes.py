"""The two shim recipes (proxy + bench) that port Atlas's hard-coded image workloads.

Each shim uploads a committed Atlas source tree (under ``tree/``) and runs its existing
``build.sh`` verbatim, so the goldens stay byte-identical on day one. These tests load the
recipes, assert the manifests parse, that the inputs/modes/phases are wired as intended,
and that the source trees actually landed under ``tree/``. They do NOT bake (no fleet).
"""

from __future__ import annotations

from pathlib import Path

from chef.engine.recipe import load_recipe

RECIPES_DIR = Path(__file__).resolve().parent.parent / "recipes"


# --- proxy ------------------------------------------------------------------------

def test_proxy_manifest_and_phases():
    recipe = load_recipe(RECIPES_DIR, "proxy")
    m = recipe.manifest

    assert m.name == "proxy"
    assert m.version == "1.0.0"
    assert m.base_image == "ubuntu-24.04"
    assert m.modes == ["cold"]
    assert [p["type"] for p in m.publish] == ["atlas-base-image", "local"]
    assert m.publish[0]["name"] == "proxy-chef"

    # proxy is cold-only: build + verify, no warm_arm.
    assert callable(recipe.load_phase("build"))
    assert callable(recipe.load_phase("verify"))
    assert recipe.has_phase("warm_arm") is False
    assert recipe.load_phase("warm_arm") is None


def test_proxy_size():
    m = load_recipe(RECIPES_DIR, "proxy").manifest
    assert m.size.vcpus == 2
    assert m.size.memory_megabytes == 1024
    assert m.size.disk_gigabytes == 20


def test_proxy_tree_uploaded():
    tree = RECIPES_DIR / "proxy" / "tree"
    assert (tree / "build.sh").is_file()
    for d in ("conf", "lua", "html", "guest", "patches"):
        assert (tree / d).is_dir(), f"missing proxy tree dir: {d}"
    # test/ + README.md are excluded from the shim upload (image_recipes.py exclude=("test",)).
    assert not (tree / "test").exists()
    assert not (tree / "README.md").exists()


# --- bench ------------------------------------------------------------------------

def test_bench_manifest_and_phases():
    recipe = load_recipe(RECIPES_DIR, "bench")
    m = recipe.manifest

    assert m.name == "bench"
    assert m.version == "16.0.0"
    assert m.base_image == "ubuntu-24.04"
    # bench bakes both cold and warm.
    assert m.modes == ["cold", "warm"]
    assert [p["type"] for p in m.publish] == ["atlas-base-image"]
    assert m.publish[0]["name"] == "bench-v16-chef"

    # All three phases resolve to callables.
    assert callable(recipe.load_phase("build"))
    assert callable(recipe.load_phase("verify"))
    assert callable(recipe.load_phase("warm_arm"))


def test_bench_size():
    m = load_recipe(RECIPES_DIR, "bench").manifest
    assert m.size.vcpus == 2
    assert m.size.memory_megabytes == 2048
    assert m.size.disk_gigabytes == 28
    assert m.size.build_memory_megabytes == 6144
    # boots fat, resizes down to memory_megabytes before snapshot.
    assert m.size.effective_build_memory_megabytes == 6144


def test_bench_input_schema_has_five_defaulted_inputs():
    recipe = load_recipe(RECIPES_DIR, "bench")
    schema = recipe.input_schema()

    props = schema["properties"]
    assert set(props) == {
        "frappe_branch",
        "erpnext_branch",
        "python_version",
        "bench_cli_ref",
        "build_mode",
    }
    # Every input has a default, so nothing is required.
    assert "required" not in schema
    assert schema["additionalProperties"] is False

    # Defaults mirror image_recipes.py bench-v16.
    resolved = recipe.validate_inputs(None)
    assert resolved == {
        "frappe_branch": "version-16",
        "erpnext_branch": "version-16",
        "python_version": "3.14",
        "bench_cli_ref": "",  # unpinned, matching _BENCH_CLI_REF
        "build_mode": "site",
    }

    # build_mode is a closed enum.
    assert props["build_mode"]["enum"] == ["site", "admin"]


def test_bench_build_mode_enum_enforced():
    import pytest

    from chef.engine.recipe import RecipeError

    recipe = load_recipe(RECIPES_DIR, "bench")
    assert recipe.validate_inputs({"build_mode": "admin"})["build_mode"] == "admin"
    with pytest.raises(RecipeError):
        recipe.validate_inputs({"build_mode": "nonsense"})


def test_bench_tree_uploaded():
    tree = RECIPES_DIR / "bench" / "tree"
    for f in (
        "build.sh",
        "warm.sh",
        "bench.toml",
        "bench.toml.md",
        "deploy-site.py",
        "bench-domain-provider.py",
        "atlas-warm-freshen.py",
    ):
        assert (tree / f).is_file(), f"missing bench tree file: {f}"
    # README.md is excluded from the shim upload.
    assert not (tree / "README.md").exists()
    assert not (tree / "__pycache__").exists()
