"""The ``chef`` recipe (decision #9: chef is itself a recipe).

``chef install-service`` bakes this recipe against pyinfra's ``@local`` connector to
install chef on a host. These tests load the recipe, assert its manifest parses, that its
two inputs default correctly, that ``build`` + ``verify`` resolve to callables, and that
the two systemd unit templates ship. They do **not** run install-service — that would
mutate the machine running the tests.
"""

from __future__ import annotations

from pathlib import Path

import chef
from chef.engine.recipe import load_recipe

RECIPES_DIR = Path(__file__).resolve().parent.parent / "recipes"


def test_chef_manifest_and_phases():
    recipe = load_recipe(RECIPES_DIR, "chef")
    m = recipe.manifest

    assert m.name == "chef"
    # The recipe version tracks the chef package version.
    assert m.version == chef.__version__
    assert m.base_image == "ubuntu-24.04"
    assert m.modes == ["cold"]
    assert [p["type"] for p in m.publish] == ["local"]

    # build + verify resolve to callables; there is no warm_arm.
    assert callable(recipe.load_phase("build"))
    assert callable(recipe.load_phase("verify"))
    assert recipe.has_phase("warm_arm") is False
    assert recipe.load_phase("warm_arm") is None


def test_chef_inputs_have_defaults():
    recipe = load_recipe(RECIPES_DIR, "chef")
    schema = recipe.input_schema()
    props = schema["properties"]

    assert set(props) == {"chef_source", "redis_url"}
    # Both inputs carry a default, so nothing is required.
    assert "required" not in schema
    assert schema["additionalProperties"] is False

    resolved = recipe.validate_inputs(None)
    assert resolved == {
        "chef_source": "git+https://github.com/frappe/chef",
        "redis_url": "redis://localhost:6379",
    }


def test_chef_templates_present():
    templates = RECIPES_DIR / "chef" / "templates"
    assert (templates / "chef-api.service.j2").is_file()
    assert (templates / "chef-worker.service.j2").is_file()
