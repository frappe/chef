"""Publisher-interface tests. The ``local`` roundtrip uses a temp file as the snapshot ref
(no fleet); the ``s3`` path is checked only for resolution + the not-configured error (no
network)."""

from __future__ import annotations

from pathlib import Path

import pytest

from chef.publishers import PublisherError, get_publisher
from chef.publishers.base import Publisher
from chef.publishers.local import LocalPublisher
from chef.publishers.s3 import S3Publisher
from chef.types import ImageLocation, SnapshotKind, SnapshotRef


def test_get_publisher_returns_registered_backends():
    assert get_publisher("local").type == "local"
    assert get_publisher("s3").type == "s3"


def test_get_publisher_unknown_raises():
    with pytest.raises(PublisherError):
        get_publisher("nope")


def test_get_publisher_atlas_lazy_error():
    with pytest.raises(PublisherError) as exc:
        get_publisher("atlas-base-image")
    assert "atlas-base-image" in str(exc.value)


def test_local_publisher_is_a_publisher():
    assert isinstance(LocalPublisher(), Publisher)


def test_local_publisher_roundtrip(tmp_path: Path):
    src = tmp_path / "snap.tar"
    payload = b"pretend-image-bytes"
    src.write_bytes(payload)

    pub = LocalPublisher(base_dir=tmp_path / "images")
    snap = SnapshotRef(kind=SnapshotKind.cold, ref=str(src), size_bytes=len(payload))

    loc = pub.publish(snap, recipe="hello", version="1.0.0", config={"type": "local"})

    assert isinstance(loc, ImageLocation)
    assert loc.type == "local"
    assert loc.uri.startswith("file://")

    dest = Path(loc.uri[len("file://"):])
    assert dest.exists()
    assert dest.read_bytes() == payload
    # landed under <base>/<recipe>/<version>/
    assert dest.parent == tmp_path / "images" / "hello" / "1.0.0"
    assert loc.manifest["recipe"] == "hello"
    assert loc.manifest["version"] == "1.0.0"
    assert loc.manifest["size_bytes"] == len(payload)


def test_local_publisher_missing_source_raises(tmp_path: Path):
    pub = LocalPublisher(base_dir=tmp_path / "images")
    snap = SnapshotRef(kind=SnapshotKind.cold, ref="local://nope-no-file")
    with pytest.raises(PublisherError):
        pub.publish(snap, recipe="hello", version="1.0.0", config={"type": "local"})


def test_s3_publisher_not_configured_raises(tmp_path: Path):
    from chef.config import Settings

    # a Settings with no credentials -> s3_configured is False
    settings = Settings(s3_access_key=None, s3_secret_key=None)
    assert settings.s3_configured is False

    src = tmp_path / "snap.tar"
    src.write_bytes(b"x")
    pub = S3Publisher(settings=settings)
    snap = SnapshotRef(kind=SnapshotKind.cold, ref=str(src))

    with pytest.raises(PublisherError) as exc:
        pub.publish(snap, recipe="hello", version="1", config={"type": "s3"})
    assert "not configured" in str(exc.value).lower()
