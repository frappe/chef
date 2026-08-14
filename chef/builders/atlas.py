"""AtlasBuilder — the production Builder, backed by Atlas's Firecracker fleet.

Chef reaches the fleet only through Atlas's whitelisted HTTP API (never boat directly):
``acquire`` asks Atlas for a blank VM (``create_bare_vm``), polls it to ``Running``, then
writes a per-bake SSH config whose ``chef-target`` host jumps through the VM's server
(``ProxyJump root@<server_ipv4>``) to reach the guest over its ``ipv6`` address. pyinfra's
``@ssh`` connector consumes that same ``ssh_config_file`` (see
:func:`chef.engine.pyinfra_runner._build_inventory` — it maps ``target.host`` +
``ssh_config_file`` onto ``ssh_hostname``/``ssh_config_file``), so the ``Host`` alias must
resolve inside the config. Snapshots come back through ``snapshot_vm`` /
``capture_warm_snapshot`` (polled to ``Available``); the capturing host's facts (from
``get_server``) are the warm image's :class:`~chef.types.HostSignature`.

The Atlas client is built in ``__init__`` (from :func:`chef.config.get_settings`), but may
be injected for tests — nothing here touches the network at import or registry-resolution
time; only actual lifecycle calls do.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from pathlib import Path

from chef.atlas_client import AtlasClient
from chef.builders import BuilderError
from chef.builders.base import Builder
from chef.config import get_settings
from chef.types import BuildSize, HostSignature, SnapshotKind, SnapshotRef, SshTarget

#: the ``Host`` alias written into the per-bake ssh config — pyinfra connects to this name.
_SSH_HOST = "chef-target"

#: statuses that mean the VM / snapshot will never become ready — fail loud instead of waiting.
_VM_DEAD_STATES = {"Failed", "Error", "Terminated", "Archived", "Broken"}
_SNAPSHOT_DEAD_STATES = {"Failed", "Error", "Broken", "Unavailable"}


class AtlasBuilder(Builder):
    name = "atlas"

    def __init__(
        self,
        client: AtlasClient | None = None,
        ssh_key_file: str | None = None,
        *,
        server: str | None = None,
        ready_timeout: float = 600,
        poll_interval: float = 5,
    ):
        settings = get_settings()
        self.client = client or AtlasClient.from_settings(settings)
        # chef's private key; its public half lives in Atlas's service_public_keys.
        self.ssh_key_file = ssh_key_file if ssh_key_file is not None else settings.atlas_ssh_key_file
        # optional: pin bakes to one Server (else Atlas placement picks one holding the image).
        self.server = server if server is not None else settings.atlas_server
        self.ready_timeout = ready_timeout
        self.poll_interval = poll_interval

    # --- lifecycle ------------------------------------------------------------

    def acquire(self, base_image: str, size: BuildSize, *, title: str) -> SshTarget:
        public_key = self._read_public_key()
        # Boot fat for a heavy build (bench asks for 6 GB build memory), resize down
        # before a WARM capture. For a cold image the memory size isn't baked in — the
        # new VM picks its own — so cold needs only the fat boot.
        created = self.client.create_bare_vm(
            title=title,
            base_image=base_image,
            vcpus=size.vcpus,
            memory_megabytes=size.effective_build_memory_megabytes,
            disk_gigabytes=size.disk_gigabytes,
            ssh_public_key=public_key,
            server=self.server,
        )
        name = created.get("name")
        if not name:
            raise BuilderError(f"atlas create_bare_vm returned no VM name: {created!r}")

        vm = self._poll_vm_running(name)
        try:
            server_ipv4 = vm.get("server_ipv4")
            server = vm.get("server")
            # Atlas's get_virtual_machine reports the guest address as `guest_ipv6`;
            # create_bare_vm returns it as `ipv6_address` — accept either.
            guest_ipv6 = vm.get("guest_ipv6") or vm.get("ipv6_address")
            if not (server_ipv4 and guest_ipv6):
                raise BuilderError(
                    f"atlas VM {name} is Running but missing routing info "
                    f"(server_ipv4={server_ipv4!r}, guest_ipv6={guest_ipv6!r})"
                )

            config_dir = Path(tempfile.mkdtemp(prefix="chef-atlas-"))
            config_path = self._write_ssh_config(config_dir, guest_ipv6, server_ipv4)
            self._wait_ssh_ready(config_path, name)
        except BaseException:
            # never leak a scratch VM if acquire fails after the VM was created
            try:
                self.client.terminate_vm(name)
            except Exception:  # noqa: BLE001
                pass
            raise

        return SshTarget(
            connector="ssh",
            host=_SSH_HOST,
            user="root",
            key_file=self.ssh_key_file,
            ssh_config_file=str(config_path),
            vm_ref=name,
            extra={
                "server_ipv4": server_ipv4,
                "server": server,
                "guest_ipv6": guest_ipv6,
                "ssh_config_dir": str(config_dir),
                # for a warm capture: shrink the fat build VM back to its restore size.
                "restore_memory_megabytes": size.memory_megabytes,
                "fattened": size.effective_build_memory_megabytes > size.memory_megabytes,
            },
        )

    def stop(self, target: SshTarget) -> None:
        self.client.stop_vm(target.vm_ref)

    def start(self, target: SshTarget) -> None:
        self.client.start_vm(target.vm_ref)

    def snapshot(self, target: SshTarget, kind: SnapshotKind, *, title: str) -> SnapshotRef:
        kind = SnapshotKind(kind)
        if kind is SnapshotKind.warm:
            snapshot_name = self.client.capture_warm_snapshot(target.vm_ref, title=title)
        else:
            snapshot_name = self.client.snapshot_vm(target.vm_ref, title=title)

        snap = self._poll_snapshot_available(snapshot_name)
        size_bytes = int(snap.get("size_bytes") or snap.get("size") or 0)

        host_signature = None
        if kind is SnapshotKind.warm:
            host_signature = self.host_signature(target)

        return SnapshotRef(
            kind=kind,
            ref=snapshot_name,
            size_bytes=size_bytes,
            host_signature=host_signature,
        )

    def host_signature(self, target: SshTarget) -> HostSignature | None:
        return HostSignature.from_dict(self.client.get_server(target.extra["server"]))

    def release(self, target: SshTarget) -> None:
        if target.vm_ref:
            try:
                self.client.terminate_vm(target.vm_ref)
            except Exception:  # noqa: BLE001 - release must be best-effort + idempotent
                pass
        config_dir = target.extra.get("ssh_config_dir")
        if config_dir:
            import shutil

            shutil.rmtree(config_dir, ignore_errors=True)

    # --- helpers --------------------------------------------------------------

    def _read_public_key(self) -> str:
        if not self.ssh_key_file:
            raise BuilderError(
                "atlas builder needs a private key — set CHEF_ATLAS_SSH_KEY_FILE (its .pub "
                "must be registered in Atlas service_public_keys)"
            )
        pub_path = Path(f"{self.ssh_key_file}.pub")
        if not pub_path.is_file():
            raise BuilderError(f"atlas ssh public key not found at {pub_path}")
        return pub_path.read_text().strip()

    def _write_ssh_config(self, config_dir: Path, guest_ipv6: str, server_ipv4: str) -> Path:
        # The ProxyJump host needs the SAME relaxed host-key handling as the guest — give it
        # its own `jump` block, not a bare `ProxyJump root@ip`. Otherwise the jump connection
        # falls back to the caller's default known_hosts + StrictHostKeyChecking, so on any
        # host whose key isn't already trusted (a fresh container — chef-as-a-service), the
        # jump dies with "Host key verification failed" and every bake hangs in acquire. A
        # laptop that has SSH'd the host before only works by accident of a warm known_hosts.
        config_path = config_dir / "ssh_config"
        config_path.write_text(
            f"Host {_SSH_HOST}\n"
            f"  HostName {guest_ipv6}\n"
            f"  User root\n"
            f"  IdentityFile {self.ssh_key_file}\n"
            f"  IdentitiesOnly yes\n"
            f"  StrictHostKeyChecking accept-new\n"
            f"  UserKnownHostsFile /dev/null\n"
            f"  ProxyJump jump\n"
            f"\n"
            f"Host jump\n"
            f"  HostName {server_ipv4}\n"
            f"  User root\n"
            f"  IdentityFile {self.ssh_key_file}\n"
            f"  IdentitiesOnly yes\n"
            f"  StrictHostKeyChecking accept-new\n"
            f"  UserKnownHostsFile /dev/null\n"
        )
        return config_path

    def _poll_vm_running(self, name: str) -> dict:
        def check() -> dict | None:
            vm = self.client.get_virtual_machine(name)
            status = vm.get("status")
            if status == "Running":
                return vm
            if status in _VM_DEAD_STATES:
                raise BuilderError(
                    f"atlas VM {name} entered terminal status {status!r} while waiting for Running"
                )
            return None

        return self._poll(check, f"atlas VM {name} did not reach Running")

    def _poll_snapshot_available(self, name: str) -> dict:
        def check() -> dict | None:
            snap = self.client.get_snapshot(name)
            status = snap.get("status")
            if status == "Available":
                return snap
            if status in _SNAPSHOT_DEAD_STATES:
                raise BuilderError(
                    f"atlas snapshot {name} entered terminal status {status!r} while waiting "
                    "for Available"
                )
            return None

        return self._poll(check, f"atlas snapshot {name} did not become Available")

    def _poll(self, check, timeout_message: str) -> dict:
        deadline = time.monotonic() + self.ready_timeout
        while True:
            result = check()
            if result is not None:
                return result
            if time.monotonic() >= deadline:
                raise BuilderError(f"{timeout_message} within {self.ready_timeout:g}s")
            time.sleep(self.poll_interval)

    def _wait_ssh_ready(self, config_path: Path, name: str) -> None:
        """Block until the system ``ssh`` (through ProxyJump) can run ``true`` on the guest."""
        deadline = time.monotonic() + self.ready_timeout
        last_error = ""
        while True:
            proc = subprocess.run(
                ["ssh", "-F", str(config_path), _SSH_HOST, "true"],
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode == 0:
                return
            last_error = (proc.stderr or proc.stdout or "").strip()
            if time.monotonic() >= deadline:
                raise BuilderError(
                    f"atlas VM {name} was not reachable over ssh within {self.ready_timeout:g}s: "
                    f"{last_error}"
                )
            time.sleep(self.poll_interval)
