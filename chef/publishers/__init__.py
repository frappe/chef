"""Publisher registry.

``get_publisher(type)`` resolves a sink by the ``[[publish]] type`` value from a recipe.
Concrete publishers are imported **lazily** so ``import chef.publishers`` never drags in
boto3 or the Atlas client unless that backend is actually used.
"""

from __future__ import annotations

from chef.publishers.base import Publisher


class PublisherError(RuntimeError):
    """Raised for an unknown/unavailable publisher or a misconfigured backend."""


def get_publisher(type: str) -> Publisher:  # noqa: A002 - matches the recipe key name
    """Resolve a :class:`Publisher` by its ``type`` ("local" | "s3" | "atlas-base-image")."""
    if type == "local":
        from chef.publishers.local import LocalPublisher

        return LocalPublisher()
    if type == "s3":
        from chef.publishers.s3 import S3Publisher

        return S3Publisher()
    if type == "atlas-base-image":
        from chef.publishers.atlas import AtlasPublisher

        return AtlasPublisher()
    if type == "atlas-s3":
        from chef.publishers.atlas import AtlasS3Publisher

        return AtlasS3Publisher()
    if type == "atlas-distribute":
        from chef.publishers.atlas import AtlasDistributePublisher

        return AtlasDistributePublisher()
    raise PublisherError(
        f"unknown publisher type {type!r} "
        "(known: local, s3, atlas-base-image, atlas-s3, atlas-distribute)"
    )
