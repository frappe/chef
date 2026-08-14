"""The Builder interface — chef's pluggable VM source + host-side snapshotter.

Packer mapping: this is the *builder*. Chef core talks only to this interface; it never
imports Atlas, Docker or any fleet. ``AtlasBuilder`` is the production default;
``DockerBuilder``/``LocalBuilder`` let recipes be authored and tested with no fleet.

Lifecycle over one bake (driven by the worker):

    target = builder.acquire(base_image, size, title=…)   # a blank, reachable VM
    # ... pyinfra runs the recipe's build+verify phases against `target` ...
    builder.stop(target)                                   # cold: clean-disk capture
    cold = builder.snapshot(target, "cold", title=…)
    builder.start(target)                                  # warm: bring the stack up
    # ... run the recipe's warm_arm phase ...
    warm = builder.snapshot(target, "warm", title=…)
    builder.release(target)                                # tear the scratch VM down
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from chef.types import BuildSize, HostSignature, SnapshotKind, SnapshotRef, SshTarget


class Builder(ABC):
    #: short backend name used in the API + logs ("atlas" | "docker" | "local")
    name: str = "base"

    @abstractmethod
    def acquire(self, base_image: str, size: BuildSize, *, title: str) -> SshTarget:
        """Provision a blank VM booted from ``base_image`` at ``size`` and return a
        reachable, SSH-ready target. Must block until the target answers."""

    @abstractmethod
    def snapshot(self, target: SshTarget, kind: SnapshotKind, *, title: str) -> SnapshotRef:
        """Capture the target's current state. ``cold`` expects a stopped VM (flush
        consistent); ``warm`` expects a running, armed VM (disk + memory at one instant)."""

    @abstractmethod
    def release(self, target: SshTarget) -> None:
        """Tear down the scratch VM. Must be safe to call once per acquire, and idempotent."""

    # --- lifecycle used by mode=warm / mode=both; default no-ops for stateless backends ---

    def stop(self, target: SshTarget) -> None:  # noqa: B027 - intentional no-op default
        """Stop the VM before a cold capture. Override where the backend needs it."""

    def start(self, target: SshTarget) -> None:  # noqa: B027 - intentional no-op default
        """Start the VM before a warm capture. Override where the backend needs it."""

    def wait_ready(self, target: SshTarget) -> None:  # noqa: B027 - intentional no-op default
        """Block until the target is reachable again after a :meth:`start`. A real VM boots
        asynchronously, so ``start`` returning does NOT mean the guest answers SSH yet — the
        warm_arm phase (and any post-start work) would otherwise race the reboot and fail with
        pyinfra's ``No hosts remaining``. No-op for stateless backends that never restart."""

    def host_signature(self, target: SshTarget) -> HostSignature | None:
        """The capturing host's CPU/kernel/Firecracker signature, for warm cross-host
        placement. ``None`` when the backend has no meaningful host family."""
        return None
