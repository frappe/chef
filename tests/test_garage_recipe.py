"""The native Garage recipe (a pyinfra port of Garage's build.sh — no shim).

These tests load the recipe and assert that the manifest parses, the inputs/modes/
phases/publish/size are wired as intended, that the build phase resolves to a
callable (and there is no warm_arm), and that the committed Garage systemd unit
actually landed under files/.

They do NOT bake (no fleet).
"""

from __future__ import annotations

from pathlib import Path

from chef.engine.recipe import load_recipe


RECIPES_DIR = Path(__file__).resolve().parent.parent / "recipes"


def test_garage_manifest_and_phases():
    recipe = load_recipe(RECIPES_DIR, "garage")
    m = recipe.manifest

    assert m.name == "garage"
    assert m.version == "1.0.0"
    assert m.base_image == "ubuntu-24.04"
    assert m.modes == ["cold"]
    assert [p["type"] for p in m.publish] == ["atlas-s3", "local"]

    # Garage is cold-only: build resolves to a callable, no warm_arm.
    assert callable(recipe.load_phase("build"))
    assert recipe.has_phase("warm_arm") is False
    assert recipe.load_phase("warm_arm") is None


def test_garage_size():
    m = load_recipe(RECIPES_DIR, "garage").manifest

    assert m.size.vcpus == 2
    assert m.size.memory_megabytes == 1024
    assert m.size.disk_gigabytes == 20


def test_garage_is_native_not_shim():
    """The old shim's tree/ (build.sh + uploaded source tree) is gone;
    the recipe and committed assets now live directly under files/.
    """
    garage = RECIPES_DIR / "garage"

    assert not (garage / "tree").exists(), (
        "recipes/garage/tree/ should be removed"
    )
    assert (garage / "files").is_dir()


def test_garage_files_assets_present():
    files = RECIPES_DIR / "garage" / "files"

    # The committed systemd unit installed by the recipe to:
    # /etc/systemd/system/garage.service
    assert (files / "guest" / "garage.service").is_file()

    # There should only be the committed guest asset under guest/.
    guest_dir = files / "guest"
    assert sorted(p.name for p in guest_dir.iterdir()) == [
        "garage.service",
    ]
