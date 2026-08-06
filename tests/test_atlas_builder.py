"""AtlasBuilder / AtlasPublisher / AtlasClient tests — no network.

The ``AtlasClient`` is fully mocked: builder/publisher tests inject a :class:`FakeAtlasClient`
that records calls and returns canned Atlas payloads, and the system ``ssh`` readiness probe
is monkeypatched to succeed. The one client-transport test uses ``httpx.MockTransport`` so
even the HTTP layer never leaves the process.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import chef.builders.atlas as atlas_builder_mod
from chef.atlas_client import AtlasClient, AtlasError
from chef.builders import get_builder
from chef.builders.atlas import AtlasBuilder
from chef.publishers import get_publisher
from chef.publishers.atlas import AtlasPublisher
from chef.types import BuildSize, HostSignature, ImageLocation, SnapshotKind, SnapshotRef, SshTarget

# --- a fake Atlas client --------------------------------------------------------


class FakeAtlasClient:
    """Records every call and returns canned Atlas responses (all VMs/snapshots ready)."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.terminated: list[str] = []
        self.vm = {
            "name": "vm-abc123",
            "status": "Running",
            "ipv6_address": "2001:db8::1234",
            "server": "atlas-host-1",
            "server_ipv4": "203.0.113.5",
        }
        self.snapshots: dict[str, dict] = {}
        self.images: dict[str, dict] = {}
        self.server = {
            "architecture": "x86_64",
            "kernel_version": "6.1.0",
            "firecracker_version": "1.7.0",
            "jailer_version": "1.7.0",
        }

    def create_bare_vm(self, **kwargs):
        self.calls.append(("create_bare_vm", kwargs))
        return {"name": self.vm["name"], "status": "Pending"}

    def get_virtual_machine(self, name):
        self.calls.append(("get_virtual_machine", name))
        return self.vm

    def stop_vm(self, vm):
        self.calls.append(("stop_vm", vm))
        return vm

    def start_vm(self, vm):
        self.calls.append(("start_vm", vm))
        return vm

    def terminate_vm(self, vm):
        self.calls.append(("terminate_vm", vm))
        self.terminated.append(vm)
        return vm

    def snapshot_vm(self, vm, title=None, live=False):
        self.calls.append(("snapshot_vm", vm, title, live))
        name = "snap-cold-1"
        self.snapshots[name] = {"name": name, "status": "Available", "size_bytes": 123456}
        return name

    def capture_warm_snapshot(self, vm, title=None):
        self.calls.append(("capture_warm_snapshot", vm, title))
        name = "snap-warm-1"
        self.snapshots[name] = {"name": name, "status": "Available", "size_bytes": 999999}
        return name

    def get_snapshot(self, name):
        self.calls.append(("get_snapshot", name))
        return self.snapshots[name]

    def promote_image(self, snapshot, image_name, title=None):
        self.calls.append(("promote_image", snapshot, image_name, title))
        self.images[image_name] = {"name": image_name, "is_active": True}
        return image_name

    def get_image(self, name):
        self.calls.append(("get_image", name))
        return self.images.get(name, {"name": name, "is_active": True})

    def get_server(self, name):
        self.calls.append(("get_server", name))
        return self.server

    def upload_image_to_s3(self, snapshot):
        self.calls.append(("upload_image_to_s3", snapshot))
        return None

    # test helpers
    def call_kwargs(self, fn):
        for entry in self.calls:
            if entry[0] == fn and isinstance(entry[1], dict):
                return entry[1]
        raise AssertionError(f"no {fn} call recorded")

    def call_names(self):
        return [entry[0] for entry in self.calls]


@pytest.fixture
def keypair(tmp_path: Path) -> Path:
    key = tmp_path / "chef_id_ed25519"
    key.write_text("PRIVATE-KEY-BYTES\n")
    (tmp_path / "chef_id_ed25519.pub").write_text("ssh-ed25519 AAAAC3Nz chef@bake\n")
    return key


@pytest.fixture
def ssh_ok(monkeypatch):
    """Make the system-ssh readiness probe succeed on the first try (no network)."""
    monkeypatch.setattr(
        atlas_builder_mod.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )


def _builder(client: FakeAtlasClient, key: Path) -> AtlasBuilder:
    return AtlasBuilder(client=client, ssh_key_file=str(key), ready_timeout=5, poll_interval=0)


# --- AtlasBuilder.acquire -------------------------------------------------------


def test_acquire_writes_valid_ssh_config_and_returns_target(keypair, ssh_ok):
    client = FakeAtlasClient()
    builder = _builder(client, keypair)

    target = builder.acquire(
        "frappe-base",
        BuildSize(vcpus=4, memory_megabytes=8192, disk_gigabytes=40),
        title="nginx-bake",
    )

    # create_bare_vm was called with the size and chef's public key.
    kw = client.call_kwargs("create_bare_vm")
    assert kw["base_image"] == "frappe-base"
    assert kw["vcpus"] == 4
    assert kw["memory_megabytes"] == 8192
    assert kw["disk_gigabytes"] == 40
    assert kw["title"] == "nginx-bake"
    assert kw["ssh_public_key"].startswith("ssh-ed25519 ")

    # a well-formed SshTarget aimed at the config's Host alias, routed for pyinfra @ssh.
    assert isinstance(target, SshTarget)
    assert target.connector == "ssh"
    assert target.host == "chef-target"
    assert target.user == "root"
    assert target.vm_ref == "vm-abc123"
    assert target.key_file == str(keypair)
    assert target.extra["server"] == "atlas-host-1"
    assert target.extra["server_ipv4"] == "203.0.113.5"
    assert target.extra["guest_ipv6"] == "2001:db8::1234"

    # the ssh config: correct hostname/ipv6 + ProxyJump through the server.
    config_text = Path(target.ssh_config_file).read_text()
    assert "Host chef-target" in config_text
    assert "HostName 2001:db8::1234" in config_text
    assert "ProxyJump root@203.0.113.5" in config_text
    assert f"IdentityFile {keypair}" in config_text
    assert "StrictHostKeyChecking accept-new" in config_text
    assert "UserKnownHostsFile /dev/null" in config_text

    # pyinfra's @ssh connector reaches the guest via this config's Host alias.
    assert target.host in config_text


def test_acquire_polls_until_running(keypair, ssh_ok):
    client = FakeAtlasClient()
    # first status Pending, then Running — acquire must keep polling.
    client.vm = dict(client.vm, status="Pending")
    states = iter(["Pending", "Provisioning", "Running"])
    original = client.get_virtual_machine

    def stepping(name):
        client.vm["status"] = next(states, "Running")
        return original(name)

    client.get_virtual_machine = stepping

    target = _builder(client, keypair).acquire("frappe-base", BuildSize(), title="t")
    assert target.vm_ref == "vm-abc123"
    assert client.call_names().count("get_virtual_machine") >= 3


def test_acquire_fails_loud_on_terminal_vm_state(keypair, ssh_ok):
    client = FakeAtlasClient()
    client.vm = dict(client.vm, status="Failed")
    from chef.builders import BuilderError

    with pytest.raises(BuilderError) as exc:
        _builder(client, keypair).acquire("frappe-base", BuildSize(), title="t")
    assert "Failed" in str(exc.value)


def test_acquire_missing_public_key_raises(tmp_path, ssh_ok):
    from chef.builders import BuilderError

    key = tmp_path / "id"
    key.write_text("priv")  # no .pub alongside it
    with pytest.raises(BuilderError):
        AtlasBuilder(client=FakeAtlasClient(), ssh_key_file=str(key)).acquire(
            "frappe-base", BuildSize(), title="t"
        )


# --- stop / start ---------------------------------------------------------------


def test_stop_and_start_call_the_client(keypair, ssh_ok):
    client = FakeAtlasClient()
    builder = _builder(client, keypair)
    target = builder.acquire("frappe-base", BuildSize(), title="t")
    builder.stop(target)
    builder.start(target)
    assert ("stop_vm", "vm-abc123") in client.calls
    assert ("start_vm", "vm-abc123") in client.calls


# --- snapshot -------------------------------------------------------------------


def test_cold_snapshot_returns_ref_without_host_signature(keypair, ssh_ok):
    client = FakeAtlasClient()
    builder = _builder(client, keypair)
    target = builder.acquire("frappe-base", BuildSize(), title="t")

    snap = builder.snapshot(target, SnapshotKind.cold, title="nginx-cold")

    assert isinstance(snap, SnapshotRef)
    assert snap.kind is SnapshotKind.cold
    assert snap.ref == "snap-cold-1"
    assert snap.size_bytes == 123456
    assert snap.host_signature is None
    assert ("snapshot_vm", "vm-abc123", "nginx-cold", False) in client.calls


def test_warm_snapshot_carries_host_signature(keypair, ssh_ok):
    client = FakeAtlasClient()
    builder = _builder(client, keypair)
    target = builder.acquire("frappe-base", BuildSize(), title="t")

    snap = builder.snapshot(target, SnapshotKind.warm, title="nginx-warm")

    assert snap.kind is SnapshotKind.warm
    assert snap.ref == "snap-warm-1"
    assert snap.size_bytes == 999999
    assert isinstance(snap.host_signature, HostSignature)
    assert snap.host_signature.architecture == "x86_64"
    assert snap.host_signature.firecracker_version == "1.7.0"
    assert ("capture_warm_snapshot", "vm-abc123", "nginx-warm") in client.calls


def test_host_signature_reads_the_capturing_server(keypair, ssh_ok):
    client = FakeAtlasClient()
    builder = _builder(client, keypair)
    target = builder.acquire("frappe-base", BuildSize(), title="t")
    sig = builder.host_signature(target)
    assert isinstance(sig, HostSignature)
    assert sig.kernel_version == "6.1.0"
    assert ("get_server", "atlas-host-1") in client.calls


# --- release --------------------------------------------------------------------


def test_release_terminates_and_removes_ssh_config(keypair, ssh_ok):
    client = FakeAtlasClient()
    builder = _builder(client, keypair)
    target = builder.acquire("frappe-base", BuildSize(), title="t")
    config_dir = Path(target.extra["ssh_config_dir"])
    assert config_dir.exists()

    builder.release(target)

    assert client.terminated == ["vm-abc123"]
    assert not config_dir.exists()


def test_release_is_best_effort_when_terminate_fails(keypair, ssh_ok):
    client = FakeAtlasClient()

    def boom(vm):
        raise AtlasError(500, "boom")

    client.terminate_vm = boom
    builder = _builder(client, keypair)
    target = builder.acquire("frappe-base", BuildSize(), title="t")
    # must not raise
    builder.release(target)


# --- AtlasPublisher -------------------------------------------------------------


def test_publisher_publish_returns_atlas_base_image_location():
    client = FakeAtlasClient()
    pub = AtlasPublisher(client=client, active_timeout=5, poll_interval=0)
    snap = SnapshotRef(kind=SnapshotKind.cold, ref="snap-cold-1", size_bytes=123)

    loc = pub.publish(snap, recipe="nginx", version="1.0.0", config={"type": "atlas-base-image", "name": "nginx"})

    assert isinstance(loc, ImageLocation)
    assert loc.type == "atlas-base-image"
    assert loc.uri == "nginx"  # uri == the config name
    assert loc.manifest == {"image_name": "nginx", "snapshot": "snap-cold-1"}
    assert ("promote_image", "snap-cold-1", "nginx", None) in client.calls
    assert ("get_image", "nginx") in client.calls


def test_publisher_requires_a_name():
    from chef.publishers import PublisherError

    pub = AtlasPublisher(client=FakeAtlasClient(), active_timeout=5, poll_interval=0)
    snap = SnapshotRef(kind=SnapshotKind.cold, ref="snap-cold-1")
    with pytest.raises(PublisherError):
        pub.publish(snap, recipe="nginx", version="1", config={"type": "atlas-base-image"})


# --- registry resolution --------------------------------------------------------


def test_get_builder_atlas_resolves(monkeypatch):
    fake = FakeAtlasClient()
    monkeypatch.setattr(AtlasClient, "from_settings", classmethod(lambda cls, settings=None: fake))
    builder = get_builder("atlas")
    assert isinstance(builder, AtlasBuilder)
    assert builder.name == "atlas"
    assert builder.client is fake


def test_get_publisher_atlas_resolves(monkeypatch):
    fake = FakeAtlasClient()
    monkeypatch.setattr(AtlasClient, "from_settings", classmethod(lambda cls, settings=None: fake))
    pub = get_publisher("atlas-base-image")
    assert isinstance(pub, AtlasPublisher)
    assert pub.type == "atlas-base-image"
    assert pub.client is fake


# --- AtlasClient transport (mocked, no network) ---------------------------------


def test_client_call_sends_token_header_and_unwraps_message():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = request.read()
        return httpx.Response(200, json={"message": {"name": "vm-1", "status": "Running"}})

    client = AtlasClient(
        "http://atlas.localhost:8000",
        "KEY",
        "SECRET",
        transport=httpx.MockTransport(handler),
    )
    result = client.create_bare_vm(
        title="t", base_image="b", vcpus=2, memory_megabytes=2048, disk_gigabytes=20
    )

    assert result == {"name": "vm-1", "status": "Running"}
    assert seen["auth"] == "token KEY:SECRET"
    assert seen["url"].endswith("/api/method/atlas.atlas.api.service.create_bare_vm")


def test_client_raises_atlas_error_on_frappe_exception():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            417,
            json={"exc_type": "ValidationError", "_server_messages": '["{\\"message\\": \\"nope\\"}"]'},
        )

    client = AtlasClient("http://a", "K", "S", transport=httpx.MockTransport(handler))
    with pytest.raises(AtlasError) as exc:
        client.get_virtual_machine("vm-x")
    assert exc.value.status == 417
    assert "nope" in exc.value.message


def test_from_settings_raises_when_unconfigured():
    from chef.config import Settings

    settings = Settings(atlas_url=None, atlas_api_key=None, atlas_api_secret=None)
    assert settings.atlas_configured is False
    with pytest.raises(AtlasError):
        AtlasClient.from_settings(settings)
