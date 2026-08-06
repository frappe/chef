"""Builder registry.

``get_builder(name)`` is the single entry point the worker uses to resolve a backend by
its short name. Concrete builders are imported **lazily** inside the function so that a
missing optional dependency (Docker CLI, the Atlas httpx client) only bites the caller who
actually asks for that backend — importing :mod:`chef.builders` stays cheap and total.
"""

from __future__ import annotations

from chef.builders.base import Builder


class BuilderError(RuntimeError):
    """Raised for an unknown/unavailable builder or a backend command failure."""


def get_builder(name: str) -> Builder:
    """Resolve a :class:`Builder` by its short ``name`` ("docker" | "local" | "atlas")."""
    if name == "docker":
        from chef.builders.docker import DockerBuilder

        return DockerBuilder()
    if name == "local":
        from chef.builders.local import LocalBuilder

        return LocalBuilder()
    if name == "atlas":
        try:
            from chef.builders.atlas import AtlasBuilder
        except ImportError as exc:  # M2 — not written yet
            raise BuilderError(
                "the 'atlas' builder is not available yet (arrives in M2)"
            ) from exc
        return AtlasBuilder()
    raise BuilderError(f"unknown builder {name!r} (known: docker, local, atlas)")
