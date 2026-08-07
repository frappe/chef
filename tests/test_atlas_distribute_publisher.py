"""AtlasDistributePublisher tests — no network.

The Atlas client is fully faked: the publisher gets a :class:`FakeClient` that records the
``publish_snapshot_as_fleet_image`` call and flips ``get_image`` to active on the second
poll, so the ``_wait_active`` loop is exercised without touching any host.
"""

from __future__ import annotations

import pytest

from chef.atlas_client import AtlasClient
from chef.publishers import PublisherError, get_publisher
from chef.publishers.atlas import AtlasDistributePublisher
from chef.types import ImageLocation, SnapshotKind, SnapshotRef


class FakeClient:
    """Records the fleet-distribute call; ``get_image`` is active from the 2nd poll on."""

    def __init__(self):
        self.publish_call = None
        self.get_image_calls = 0

    def publish_snapshot_as_fleet_image(self, *, snapshot, image_name, servers=None):
        self.publish_call = {"snapshot": snapshot, "image_name": image_name, "servers": servers}
        return {"image": image_name, "rootfs_sha256": "...", "kernel_sha256": "...", "tasks": []}

    def get_image(self, name):
        self.get_image_calls += 1
        return {"name": name, "is_active": self.get_image_calls >= 2}


def test_atlas_distribute_publisher_publishes_to_fleet(monkeypatch):
    client = FakeClient()
    pub = AtlasDistributePublisher(client=client, active_timeout=5, poll_interval=0)
    snap = SnapshotRef(kind=SnapshotKind.cold, ref="snap-x")

    loc = pub.publish(
        snap,
        recipe="base",
        version="1.0.0",
        config={"type": "atlas-distribute", "name": "chef-base", "servers": ["srv-1", "srv-2"]},
    )

    assert isinstance(loc, ImageLocation)
    assert loc.type == "atlas-distribute"
    assert loc.uri == "chef-base"  # uri == the config name
    assert loc.manifest == {"image_name": "chef-base", "snapshot": "snap-x"}
    assert client.publish_call == {
        "snapshot": "snap-x",
        "image_name": "chef-base",
        "servers": ["srv-1", "srv-2"],
    }
    assert client.get_image_calls == 2  # polled until is_active flipped

    # the registry resolves the new type to this publisher.
    monkeypatch.setattr(AtlasClient, "from_settings", classmethod(lambda cls, settings=None: client))
    resolved = get_publisher("atlas-distribute")
    assert isinstance(resolved, AtlasDistributePublisher)
    assert resolved.type == "atlas-distribute"
    assert resolved.client is client


def test_atlas_distribute_publisher_requires_a_name():
    pub = AtlasDistributePublisher(client=FakeClient(), active_timeout=5, poll_interval=0)
    snap = SnapshotRef(kind=SnapshotKind.cold, ref="snap-x")
    with pytest.raises(PublisherError):
        pub.publish(snap, recipe="base", version="1", config={"type": "atlas-distribute"})
