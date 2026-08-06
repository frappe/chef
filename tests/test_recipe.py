"""Recipe loading/validation/phase-resolution against the shipped ``hello`` + ``nginx``."""

from __future__ import annotations

from pathlib import Path

import pytest

from chef.engine.recipe import RecipeError, load_recipe

RECIPES_DIR = Path(__file__).resolve().parent.parent / "recipes"


def test_hello_manifest_and_phases():
    recipe = load_recipe(RECIPES_DIR, "hello")
    m = recipe.manifest

    assert m.name == "hello"
    assert m.version == "1.0.0"
    assert m.base_image == "ubuntu-24.04"
    assert m.modes == ["cold"]
    assert m.phases["build"] == "recipe:build"
    assert m.publish == [{"type": "local"}]

    assert recipe.has_phase("build") is True
    assert recipe.has_phase("verify") is False
    assert callable(recipe.load_phase("build"))
    assert recipe.load_phase("verify") is None

    # hello declares no inputs: an empty, closed object schema.
    schema = recipe.input_schema()
    assert schema["type"] == "object"
    assert schema["properties"] == {}
    assert schema["additionalProperties"] is False
    assert "required" not in schema
    assert recipe.validate_inputs(None) == {}


def test_nginx_manifest_and_phases():
    recipe = load_recipe(RECIPES_DIR, "nginx")
    m = recipe.manifest

    assert m.name == "nginx"
    assert m.base_image == "ubuntu-24.04"
    assert m.modes == ["cold"]
    assert [p["type"] for p in m.publish] == ["local", "s3"]

    assert recipe.has_phase("verify") is True
    assert callable(recipe.load_phase("build"))
    assert callable(recipe.load_phase("verify"))


def test_nginx_input_schema_shape():
    recipe = load_recipe(RECIPES_DIR, "nginx")
    schema = recipe.input_schema()

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    wp = schema["properties"]["worker_processes"]
    assert wp["type"] == "string"
    assert wp["default"] == "auto"
    assert "description" in wp
    # worker_processes has a default, so nothing is required.
    assert "required" not in schema


def test_nginx_validate_inputs_fills_defaults():
    recipe = load_recipe(RECIPES_DIR, "nginx")
    assert recipe.validate_inputs(None) == {"worker_processes": "auto"}
    assert recipe.validate_inputs({"worker_processes": "4"}) == {"worker_processes": "4"}


def test_nginx_validate_inputs_rejects_bad_value():
    recipe = load_recipe(RECIPES_DIR, "nginx")

    # Wrong type: worker_processes must be a string.
    with pytest.raises(RecipeError):
        recipe.validate_inputs({"worker_processes": 4})

    # Unknown key: schema is closed (additionalProperties=False).
    with pytest.raises(RecipeError):
        recipe.validate_inputs({"nope": "x"})
