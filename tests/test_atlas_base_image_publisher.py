"""AtlasPublisher (``atlas-base-image``) tests — promote, and the opt-in fleet distribute.

The Atlas client is fully faked: the publisher records ``promote_image`` +
``distribute_image`` and flips ``get_image`` to active on the second poll, so both the
``_wait_active`` loop and the ``distribute = true`` branch run without touching a host.
"""

from __future__ import annotations

from chef.publishers.atlas import AtlasPublisher
from chef.types import ImageLocation, SnapshotKind, SnapshotRef


class FakeClient:
    def __init__(self):
        self.promote_call = None
        self.distribute_call = None
        self.get_image_calls = 0

    def promote_image(self, *, snapshot, image_name, title=None):
        self.promote_call = {"snapshot": snapshot, "image_name": image_name}
        return image_name

    def distribute_image(self, image, servers=None):
        self.distribute_call = {"image": image, "servers": servers}
        return {"image": image, "source": "host-1", "servers": ["host-2", "host-3"]}

    def get_image(self, name):
        self.get_image_calls += 1
        return {"name": name, "is_active": self.get_image_calls >= 2}


def _snap():
    return SnapshotRef(kind=SnapshotKind.cold, ref="snap-x")


def test_atlas_publisher_promotes_without_distributing_by_default():
    client = FakeClient()
    pub = AtlasPublisher(client=client, active_timeout=5, poll_interval=0)

    loc = pub.publish(_snap(), recipe="pilot", version="0.1.0", config={"name": "pilot-chef"})

    assert isinstance(loc, ImageLocation)
    assert loc.uri == "pilot-chef"
    assert client.promote_call == {"snapshot": "snap-x", "image_name": "pilot-chef"}
    assert client.distribute_call is None  # no fan-out unless the block asks for it
    assert loc.manifest["distributed_to"] is None
    assert client.get_image_calls == 2  # polled until is_active flipped


def test_atlas_publisher_distributes_when_configured():
    client = FakeClient()
    pub = AtlasPublisher(client=client, active_timeout=5, poll_interval=0)

    loc = pub.publish(
        _snap(),
        recipe="pilot",
        version="0.1.0",
        config={"name": "pilot-chef", "distribute": True},
    )

    assert client.distribute_call == {"image": "pilot-chef", "servers": None}
    assert loc.manifest["distributed_to"] == ["host-2", "host-3"]


def test_atlas_publisher_distribute_passes_explicit_servers():
    client = FakeClient()
    pub = AtlasPublisher(client=client, active_timeout=5, poll_interval=0)

    pub.publish(
        _snap(),
        recipe="pilot",
        version="0.1.0",
        config={"name": "pilot-chef", "distribute": True, "servers": ["host-2"]},
    )

    assert client.distribute_call == {"image": "pilot-chef", "servers": ["host-2"]}
