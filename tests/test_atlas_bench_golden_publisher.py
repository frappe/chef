"""AtlasBenchGoldenPublisher (``atlas-bench-snapshot``) tests — register the cold snapshot
as Atlas's ``default_bench_snapshot`` (the golden a self-serve Site clones from).

The Atlas client is faked: the publisher just records the ``register_bench_snapshot`` call.
"""

from __future__ import annotations

from chef.publishers.atlas import AtlasBenchGoldenPublisher
from chef.types import ImageLocation, SnapshotKind, SnapshotRef


class FakeClient:
    def __init__(self):
        self.register_bench_snapshot_call = None

    def register_bench_snapshot(self, snapshot):
        self.register_bench_snapshot_call = snapshot
        return snapshot


def test_registers_the_cold_snapshot_as_default_bench_snapshot():
    client = FakeClient()
    pub = AtlasBenchGoldenPublisher(client=client)

    loc = pub.publish(
        SnapshotRef(kind=SnapshotKind.cold, ref="snap-cold"),
        recipe="pilot",
        version="0.1.0",
        config={"type": "atlas-bench-snapshot"},
    )

    assert client.register_bench_snapshot_call == "snap-cold"
    assert isinstance(loc, ImageLocation)
    assert loc.type == "atlas-bench-snapshot"
    assert loc.uri == "snap-cold"
    assert loc.manifest["default_bench_snapshot"] == "snap-cold"


def test_only_consumes_cold_snapshots():
    # The pipeline (bake_job) skips a publisher whose .kinds excludes the current kind;
    # a warm snapshot is auto-discovered by Atlas, never registered as the cold pointer.
    assert AtlasBenchGoldenPublisher.kinds == (SnapshotKind.cold,)
