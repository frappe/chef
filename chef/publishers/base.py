"""The Publisher interface — chef's pluggable image sink.

Packer mapping: this is the *post-processor*. A bake may run several (0..n), one per
``[[publish]]`` block in the recipe manifest. Chef core talks only to this interface.

Backends: ``LocalPublisher`` (dev, copies bytes to a local dir), ``S3Publisher``
(S3-compatible object store), ``AtlasPublisher`` (promote/register a host-side base
image via Atlas's API).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from chef.types import ImageLocation, SnapshotKind, SnapshotRef


class Publisher(ABC):
    #: the ``[[publish]] type = "…"`` value this publisher handles
    type: str = "base"

    #: builder names whose snapshot representation this publisher can consume, or ``None``
    #: for any. A recipe lists every destination it *wants*; the bake pipeline runs only
    #: the publishers compatible with the active builder (e.g. LocalPublisher uploads a
    #: local tar and can't consume an Atlas snapshot reference), skipping the rest.
    builders: tuple[str, ...] | None = None

    #: snapshot kinds this publisher can consume, or ``None`` for any. A ``both`` bake takes
    #: a cold *and* a warm snapshot and runs every publish block against each; a publisher
    #: that only makes sense for one kind (e.g. ``atlas-base-image`` promotes a base image,
    #: which a warm memory snapshot can't be) declares it here so the pipeline skips the
    #: mismatched pairing instead of failing the bake.
    kinds: tuple[SnapshotKind, ...] | None = None

    @abstractmethod
    def publish(
        self,
        snapshot: SnapshotRef,
        *,
        recipe: str,
        version: str,
        config: dict,
    ) -> ImageLocation:
        """Send ``snapshot``'s bytes to this destination and return where they landed.

        ``config`` is the recipe's ``[[publish]]`` block verbatim (e.g.
        ``{"type": "atlas-base-image", "name": "nginx"}``) — target names come from the
        recipe, never from chef-core constants (decision #18)."""
