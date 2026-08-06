"""Recipe loading, validation and phase resolution.

A recipe is a directory: ``recipe.py`` (pyinfra ``@deploy`` phase callables) +
``recipe.toml`` (the manifest) + optional ``templates/``. This module is the only place
that understands that on-disk shape. It:

  * parses + normalises ``recipe.toml`` into a :class:`Manifest`,
  * builds a JSON Schema from the manifest's ``[inputs.*]`` and validates/defaults a
    caller's inputs against it,
  * imports the phase callables named by ``[phases]`` (``module:callable``).

``POST /recipes/validate`` runs load + schema + import without baking (the agent
write→fix loop); ``GET /recipes/template`` emits the skeleton below.
"""

from __future__ import annotations

import importlib.util
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import jsonschema

from chef.types import BuildSize

# JSON-Schema keys we copy verbatim from an ``[inputs.<name>]`` table into the property.
_SCHEMA_KEYS = (
    "type", "description", "enum", "minimum", "maximum",
    "minLength", "maxLength", "pattern", "items", "format",
)


class RecipeError(Exception):
    """Raised for any malformed recipe (bad TOML, missing phase, import failure)."""

    def __init__(self, message: str, *, field: str | None = None, line: int | None = None):
        super().__init__(message)
        self.message = message
        self.field = field
        self.line = line

    def as_dict(self) -> dict:
        return {"message": self.message, "field": self.field, "line": self.line}


@dataclass
class Manifest:
    name: str
    version: str
    description: str
    base_image: str
    modes: list[str]
    tags: list[str]
    phases: dict[str, str]          # {"build": "recipe:build", "verify": "...", "warm_arm": ""}
    size: BuildSize
    inputs: dict[str, dict]         # raw [inputs.*] tables
    publish: list[dict]             # [[publish]] blocks
    path: Path = field(default=Path("."))
    compose: list[str] = field(default_factory=list)   # base recipes this one stacks, in order
    modes_declared: bool = False    # did the .toml set `modes` (vs. the ["cold"] default)?


class Recipe:
    """A loaded recipe: its manifest, its directory, and lazy access to phase callables."""

    def __init__(self, manifest: Manifest):
        self.manifest = manifest
        self.path = manifest.path
        # The linearized recipes this one is built from, base-first, ending with self.
        # A plain (non-composed) recipe is a stack of one; :func:`load_recipe` replaces
        # this with the resolved stack for a ``compose = [...]`` recipe.
        self.stack: list[Recipe] = [self]

    @property
    def lineage(self) -> list[str]:
        """Names of every recipe in the resolved stack, base-first, ending with self."""
        return [leaf.manifest.name for leaf in self.stack]

    # --- inputs ---------------------------------------------------------------

    def input_schema(self) -> dict:
        """A JSON Schema (draft 2020-12 compatible) built from the manifest inputs."""
        properties: dict[str, dict] = {}
        required: list[str] = []
        for name, spec in self.manifest.inputs.items():
            prop = {k: spec[k] for k in _SCHEMA_KEYS if k in spec}
            prop.setdefault("type", "string")
            if "default" in spec:
                prop["default"] = spec["default"]
            else:
                required.append(name)
            properties[name] = prop
        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
        if required:
            schema["required"] = required
        return schema

    def validate_inputs(self, inputs: dict | None) -> dict:
        """Fill defaults, then validate against :meth:`input_schema`. Returns the resolved
        inputs. Raises :class:`RecipeError` on a schema violation."""
        resolved = {
            name: spec["default"]
            for name, spec in self.manifest.inputs.items()
            if "default" in spec
        }
        resolved.update(inputs or {})
        try:
            jsonschema.validate(resolved, self.input_schema())
        except jsonschema.ValidationError as exc:
            field = ".".join(str(p) for p in exc.absolute_path) or None
            raise RecipeError(exc.message, field=field) from exc
        return resolved

    # --- phases ---------------------------------------------------------------

    def load_phase(self, phase: str) -> Callable | None:
        """Import + return the ``@deploy`` callable for ``phase`` (build/verify/warm_arm),
        or ``None`` when the manifest leaves it empty."""
        ref = self.manifest.phases.get(phase, "")
        if not ref:
            return None
        module_name, _, attr = ref.partition(":")
        if not module_name or not attr:
            raise RecipeError(f"phase '{phase}' must be 'module:callable', got {ref!r}",
                              field=f"phases.{phase}")
        module_path = self.path / f"{module_name}.py"
        if not module_path.exists():
            raise RecipeError(f"phase '{phase}' module not found: {module_path.name}",
                              field=f"phases.{phase}")
        mod = _import_module(module_path, f"chef_recipe_{self.manifest.name}_{module_name}")
        fn = getattr(mod, attr, None)
        if fn is None:
            raise RecipeError(f"phase '{phase}' callable '{attr}' not found in {module_path.name}",
                              field=f"phases.{phase}")
        return fn

    def has_phase(self, phase: str) -> bool:
        """True when any recipe in the stack defines ``phase`` (base or self)."""
        return any(leaf.manifest.phases.get(phase, "") for leaf in self.stack)

    def phase_chain(self, phase: str) -> list[tuple[str, Callable]]:
        """The ``(recipe_name, @deploy)`` callables to run for ``phase``, in stack order:
        each base's own phase first, this recipe's own phase last. Empties are skipped."""
        chain: list[tuple[str, Callable]] = []
        for leaf in self.stack:
            fn = leaf.load_phase(phase)
            if fn is not None:
                chain.append((leaf.manifest.name, fn))
        return chain

    def phase_sources(self) -> dict[str, list[str]]:
        """For each phase, the ordered recipe names that contribute ops (for the API/UI)."""
        out: dict[str, list[str]] = {}
        for phase in ("build", "verify", "warm_arm"):
            names = [leaf.manifest.name for leaf in self.stack
                     if leaf.manifest.phases.get(phase, "")]
            if names:
                out[phase] = names
        return out

    def _own_source(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for p in sorted(self.path.rglob("*")):
            if p.is_file() and p.suffix in (".py", ".toml", ".j2", ".sh", ".conf", ".md"):
                out[str(p.relative_to(self.path))] = p.read_text()
        return out

    def source(self) -> dict[str, str]:
        """All human-readable source files, for API display. For a composed recipe every
        stacked recipe's files are included, keyed ``<recipe>/<relpath>`` so the UI shows
        exactly which recipe each file came from."""
        if len(self.stack) == 1:
            return self._own_source()
        out: dict[str, str] = {}
        for leaf in self.stack:
            for rel, text in leaf._own_source().items():
                out[f"{leaf.manifest.name}/{rel}"] = text
        return out


def _import_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RecipeError(f"could not import {path}")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:  # noqa: BLE001 - surface any import error as a recipe error
        raise RecipeError(f"import failed: {exc}", field="recipe.py") from exc
    return mod


def _parse_manifest(path: Path) -> Manifest:
    toml_path = path / "recipe.toml"
    if not toml_path.exists():
        raise RecipeError(f"missing recipe.toml in {path}")
    try:
        data = tomllib.loads(toml_path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise RecipeError(f"invalid recipe.toml: {exc}", field="recipe.toml") from exc

    size_raw = data.get("size", {})
    size = BuildSize(
        vcpus=int(size_raw.get("vcpus", 2)),
        memory_megabytes=int(size_raw.get("memory_megabytes", 2048)),
        disk_gigabytes=int(size_raw.get("disk_gigabytes", 20)),
        build_memory_megabytes=int(size_raw.get("build_memory_megabytes", 0)),
    )
    phases = data.get("phases", {})
    compose = list(data.get("compose", []))
    # A pure-composition recipe inherits base_image + its build phase from the recipes it
    # stacks, so those become optional once `compose` names at least one base.
    for required in ("name", "version"):
        if not data.get(required):
            raise RecipeError(f"recipe.toml missing required key '{required}'", field=required)
    if not compose and not data.get("base_image"):
        raise RecipeError("recipe.toml missing required key 'base_image'", field="base_image")
    if not compose and not phases.get("build"):
        raise RecipeError("recipe.toml [phases] must define 'build'", field="phases.build")

    return Manifest(
        name=data["name"],
        version=str(data["version"]),
        description=data.get("description", ""),
        base_image=data.get("base_image", ""),
        modes=list(data.get("modes", ["cold"])),
        tags=list(data.get("tags", [])),
        phases={
            "build": phases.get("build", ""),
            "verify": phases.get("verify", ""),
            "warm_arm": phases.get("warm_arm", ""),
        },
        size=size,
        inputs=dict(data.get("inputs", {})),
        publish=list(data.get("publish", [])),
        path=path,
        compose=compose,
        modes_declared="modes" in data,
    )


def load_recipe(recipes_dir: Path, name: str) -> Recipe:
    path = Path(recipes_dir) / name
    if not path.is_dir():
        raise RecipeError(f"recipe '{name}' not found")
    return Recipe(_parse_manifest(path))


def list_manifests(recipes_dir: Path) -> list[Manifest]:
    recipes_dir = Path(recipes_dir)
    out: list[Manifest] = []
    if not recipes_dir.is_dir():
        return out
    for child in sorted(recipes_dir.iterdir()):
        if child.is_dir() and (child / "recipe.toml").exists():
            try:
                out.append(_parse_manifest(child))
            except RecipeError:
                continue  # a broken recipe shouldn't hide the healthy ones in a listing
    return out


RECIPE_TOML_TEMPLATE = '''\
name        = "myrecipe"
version     = "1.0.0"
description = "What this image is"
base_image  = "ubuntu-24.04"           # a name the chosen Builder understands
modes       = ["cold"]                  # or ["cold", "warm"]
tags        = []

[phases]
build     = "recipe:build"              # required — module:callable pyinfra @deploy
verify    = ""                          # optional — fail-loud gate before snapshot
warm_arm  = ""                          # optional — run before a warm capture

[size]
vcpus = 2
memory_megabytes = 2048
disk_gigabytes = 20
build_memory_megabytes = 0             # boot fat for a heavy build, resize down before snapshot

# [inputs.example]                      # arbitrary, JSON-Schema-shaped, validated on bake
# type = "string"
# default = "value"
# description = "what it controls"

[[publish]]                            # where produced images go; 0..n, Publisher-typed
type = "local"
'''

RECIPE_PY_TEMPLATE = '''\
# recipe.py — pure pyinfra, no chef/atlas imports
from pyinfra import host
from pyinfra.api import deploy
from pyinfra.operations import server


@deploy("build")
def build():
    server.shell(
        name="hello",
        commands=["echo baking on $(hostname)"],
        _retries=3,
        _retry_delay=10,
    )


@deploy("verify")
def verify():
    server.shell(name="it works", commands=["true"])
'''


def recipe_template() -> dict[str, str]:
    """The skeleton emitted by ``GET /recipes/template``."""
    return {"recipe.toml": RECIPE_TOML_TEMPLATE, "recipe.py": RECIPE_PY_TEMPLATE}
