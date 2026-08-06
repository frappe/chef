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
    builders = ("atlas",)  # promotes an Atlas-side snapshot reference

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


#: S3 uploads move ~20 GB over the host's curl+zstd transport, so allow longer.
_UPLOAD_TIMEOUT = 3600


class AtlasS3Publisher(Publisher):
    """Back a snapshot up to Atlas's S3 store (a durable, off-host copy of the golden).

    Unlike ``AtlasPublisher`` (which promotes an on-host base image), this uploads the
    snapshot's bytes to the object store via Atlas's presigned-URL + host ``curl``+``zstd``
    transport and leaves nothing on the host — the snapshot is torn down when the bake
    releases the scratch VM, but the S3 copy persists (restorable via Atlas ``restore``).
    """

    type = "atlas-s3"
    builders = ("atlas",)  # uploads an Atlas-side snapshot reference

    def __init__(
        self,
        client: AtlasClient | None = None,
        *,
        upload_timeout: float = _UPLOAD_TIMEOUT,
        poll_interval: float = _POLL_INTERVAL,
    ):
        self.client = client or AtlasClient.from_settings()
        self.upload_timeout = upload_timeout
        self.poll_interval = poll_interval

    def publish(
        self,
        snapshot: SnapshotRef,
        *,
        recipe: str,  # noqa: ARG002 - Atlas keys the object by the snapshot
        version: str,  # noqa: ARG002
        config: dict,  # noqa: ARG002 - no atlas-s3-specific knobs
    ) -> ImageLocation:
        self.client.upload_image_to_s3(snapshot=snapshot.ref)
        snap = self._wait_uploaded(snapshot.ref)
        return ImageLocation(
            type="atlas-s3",
            uri=f"atlas-s3:{snapshot.ref}",
            manifest={"snapshot": snapshot.ref, "s3_status": snap.get("s3_status")},
        )

    def _wait_uploaded(self, snapshot_name: str) -> dict:
        deadline = time.monotonic() + self.upload_timeout
        while True:
            snap = self.client.get_snapshot(snapshot_name)
            status = (snap.get("s3_status") or "").lower()
            if status == "uploaded":
                return snap
            if status == "failed":
                raise PublisherError(f"atlas S3 upload of snapshot {snapshot_name!r} failed")
            if time.monotonic() >= deadline:
                raise PublisherError(
                    f"atlas S3 upload of snapshot {snapshot_name!r} did not finish within "
                    f"{self.upload_timeout:g}s (last s3_status={snap.get('s3_status')!r})"
                )
            time.sleep(self.poll_interval)
