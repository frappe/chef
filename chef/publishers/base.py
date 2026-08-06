"""The Publisher interface — chef's pluggable image sink.

Packer mapping: this is the *post-processor*. A bake may run several (0..n), one per
``[[publish]]`` block in the recipe manifest. Chef core talks only to this interface.

Backends: ``LocalPublisher`` (dev, copies bytes to a local dir), ``S3Publisher``
(S3-compatible object store), ``AtlasPublisher`` (promote/register a host-side base
image via Atlas's API).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from chef.types import ImageLocation, SnapshotRef


class Publisher(ABC):
    #: the ``[[publish]] type = "…"`` value this publisher handles
    type: str = "base"

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
