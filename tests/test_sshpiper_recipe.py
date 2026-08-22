"""Tests for the native SSH Piper pyinfra recipe.

These tests load the recipe and assert that the manifest parses, the inputs/
modes/phases/publish/size are wired as intended, that the build phase resolves
to a callable (and there is no warm_arm), and that the committed Go source,
crypto submodule, and guest configuration assets actually exist.

They do NOT bake (no fleet).
"""

from __future__ import annotations

from pathlib import Path

from chef.engine.recipe import load_recipe


RECIPES_DIR = Path(__file__).resolve().parent.parent / "recipes"


def test_sshpiper_manifest_and_phases():
    recipe = load_recipe(RECIPES_DIR, "sshpiper")
    m = recipe.manifest

    assert m.name == "sshpiper"
    assert m.version == "1.0.0"
    assert m.base_image == "ubuntu-24.04"
    assert m.modes == ["cold"]
    assert [p["type"] for p in m.publish] == ["atlas-s3", "local"]

    # SSH Piper is cold-only: build resolves to a callable, no warm_arm.
    assert callable(recipe.load_phase("build"))
    assert recipe.has_phase("warm_arm") is False
    assert recipe.load_phase("warm_arm") is None


def test_sshpiper_size():
    m = load_recipe(RECIPES_DIR, "sshpiper").manifest

    assert m.size.vcpus == 2
    assert m.size.memory_megabytes == 1024
    assert m.size.disk_gigabytes == 20


def test_sshpiper_is_native_not_shim():
    """The old tree/ build.sh shim is gone; the Go source and guest assets
    now live directly under the recipe.
    """
    sshpiper = RECIPES_DIR / "sshpiper"

    assert not (sshpiper / "tree").exists(), (
        "recipes/sshpiper/tree/ should be removed"
    )

    assert (sshpiper / "atlas-sshpiper").is_dir()
    assert (sshpiper / "guest").is_dir()


def test_sshpiper_go_source_present():
    """The Atlas SSH Piper implementation is built from the committed Go tree."""
    source = RECIPES_DIR / "sshpiper" / "atlas-sshpiper"

    assert (source / "go.mod").is_file()
    assert (source / "go.sum").is_file()

    # There must be Go source in the root build context.
    go_files = sorted(source.glob("*.go"))
    assert go_files, "atlas-sshpiper/ should contain Go source files"


def test_sshpiper_crypto_submodule_present():
    """sshpiper.crypto is part of the Go build context rather than a separate
    repository-level tree.
    """
    source = RECIPES_DIR / "sshpiper" / "atlas-sshpiper"
    crypto = source / "sshpiper.crypto"

    assert crypto.is_dir(), (
        "atlas-sshpiper/sshpiper.crypto/ should be present"
    )

    # Ensure the submodule isn't merely an empty directory.
    assert any(crypto.iterdir()), (
        "atlas-sshpiper/sshpiper.crypto/ should contain its source"
    )


def test_sshpiper_guest_assets_present():
    files = RECIPES_DIR / "sshpiper" / "guest"

    assert (files / "sshpiper.service").is_file()
    assert (files / "60-atlas-sshpiper.conf").is_file()

    # The guest directory should contain exactly the two committed files
    # consumed by recipe.py.
    assert sorted(p.name for p in files.iterdir()) == [
        "60-atlas-sshpiper.conf",
        "sshpiper.service",
    ]
