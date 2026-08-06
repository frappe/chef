"""Builder-interface tests. Exercised entirely through the ``local`` backend so they need
neither Docker nor a fleet; the docker/atlas paths are checked only for resolution + clear
errors."""

from __future__ import annotations

import pytest

from chef.builders import BuilderError, get_builder
from chef.builders.base import Builder
from chef.builders.docker import _docker_image
from chef.builders.local import LocalBuilder
from chef.types import BuildSize, SnapshotKind, SnapshotRef, SshTarget


def test_get_builder_returns_registered_backends():
    assert get_builder("local").name == "local"
    # docker resolves without touching the daemon (no docker calls at construction time)
    assert get_builder("docker").name == "docker"


def test_get_builder_unknown_raises():
    with pytest.raises(BuilderError):
        get_builder("nope")


def test_get_builder_atlas_needs_config():
    # atlas now resolves (M2 landed), but constructing it unconfigured fails clearly.
    from chef.atlas_client import AtlasError

    with pytest.raises(AtlasError) as exc:
        get_builder("atlas")
    assert "not configured" in str(exc.value).lower()


def test_local_builder_is_a_builder():
    assert isinstance(get_builder("local"), Builder)


def test_local_builder_acquire_shape():
    b = LocalBuilder()
    target = b.acquire("ubuntu-24.04", BuildSize(), title="demo")
    assert isinstance(target, SshTarget)
    assert target.connector == "local"
    assert target.host == "@local"
    assert target.vm_ref == "local"


def test_local_builder_snapshot_is_a_stub_ref():
    b = LocalBuilder()
    target = b.acquire("ubuntu-24.04", BuildSize(), title="demo")
    snap = b.snapshot(target, SnapshotKind.cold, title="my-title")
    assert isinstance(snap, SnapshotRef)
    assert snap.kind is SnapshotKind.cold
    assert snap.ref == "local://my-title"


def test_local_builder_lifecycle_noops_do_not_raise():
    b = LocalBuilder()
    target = b.acquire("ubuntu-24.04", BuildSize(), title="demo")
    # stop/start/release are all no-ops for the local backend
    b.stop(target)
    b.start(target)
    b.release(target)
    b.release(target)  # idempotent


@pytest.mark.parametrize(
    ("chef_name", "docker_ref"),
    [
        ("ubuntu-24.04", "ubuntu:24.04"),
        ("ubuntu-22.04", "ubuntu:22.04"),
        ("debian-12", "debian:12"),
        ("", "ubuntu:24.04"),          # default
        ("ubuntu:24.04", "ubuntu:24.04"),  # already a docker ref
    ],
)
def test_docker_image_name_mapping(chef_name, docker_ref):
    assert _docker_image(chef_name) == docker_ref
