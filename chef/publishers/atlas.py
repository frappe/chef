"""AtlasPublisher — promote a snapshot into a named Atlas base image.

The M2 production sink: hand a :class:`~chef.types.SnapshotRef` (an Atlas snapshot name)
to Atlas's ``promote_image`` under the base-image name the recipe chose
(``config["name"]`` — target names come from the recipe, never chef-core constants), poll
the image to ``is_active``, and return an ``atlas-base-image``
:class:`~chef.types.ImageLocation` whose ``uri`` *is* that base-image name.

It builds its own Atlas client from :func:`chef.config.get_settings` (injectable for tests);
nothing here touches the network at import time.
"""

from __future__ import annotations

import time

from chef.atlas_client import AtlasClient
from chef.publishers import PublisherError
from chef.publishers.base import Publisher
from chef.types import ImageLocation, SnapshotRef

#: how long to wait for a promoted image to report ``is_active``.
_ACTIVE_TIMEOUT = 600
_POLL_INTERVAL = 5


class AtlasPublisher(Publisher):
    type = "atlas-base-image"

    def __init__(
        self,
        client: AtlasClient | None = None,
        *,
        active_timeout: float = _ACTIVE_TIMEOUT,
        poll_interval: float = _POLL_INTERVAL,
    ):
        self.client = client or AtlasClient.from_settings()
        self.active_timeout = active_timeout
        self.poll_interval = poll_interval

    def publish(
        self,
        snapshot: SnapshotRef,
        *,
        recipe: str,  # noqa: ARG002 - the base-image name comes from the recipe's publish block
        version: str,  # noqa: ARG002 - Atlas versions base images itself
        config: dict,
    ) -> ImageLocation:
        image_name = config.get("name")
        if not image_name:
            raise PublisherError(
                "atlas-base-image publish block needs a 'name' (the base-image name)"
            )

        self.client.promote_image(snapshot=snapshot.ref, image_name=image_name)
        self._wait_active(image_name)

        return ImageLocation(
            type="atlas-base-image",
            uri=image_name,
            manifest={"image_name": image_name, "snapshot": snapshot.ref},
        )

    def _wait_active(self, image_name: str) -> None:
        deadline = time.monotonic() + self.active_timeout
        while True:
            image = self.client.get_image(image_name)
            if image.get("is_active"):
                return
            if time.monotonic() >= deadline:
                raise PublisherError(
                    f"atlas image {image_name!r} did not become active within "
                    f"{self.active_timeout:g}s"
                )
            time.sleep(self.poll_interval)
