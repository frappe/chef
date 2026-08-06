"""Internal contract types shared across engine, builders, publishers and the worker.

These are deliberately plain dataclasses/enums (no Pydantic, no DB) so every layer can
import them without pulling in FastAPI or SQLModel. The FastAPI request/response models
live in ``chef.schemas``; the SQLite rows in ``chef.store``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Mode(str, Enum):
    """What a bake produces. ``both`` = one scratch VM, cold-then-warm (decision #7)."""

    cold = "cold"
    warm = "warm"
    both = "both"

    def kinds(self) -> list["SnapshotKind"]:
        if self is Mode.both:
            return [SnapshotKind.cold, SnapshotKind.warm]
        return [SnapshotKind(self.value)]


class SnapshotKind(str, Enum):
    cold = "cold"
    warm = "warm"


class BakeState(str, Enum):
    queued = "queued"
    acquiring = "acquiring"      # builder.acquire — getting a blank VM
    building = "building"        # running the build phase ops
    verifying = "verifying"      # running the verify phase (fail-loud gate)
    snapshotting = "snapshotting"
    publishing = "publishing"
    succeeded = "succeeded"
    failed = "failed"
    aborted = "aborted"

    @property
    def terminal(self) -> bool:
        return self in (BakeState.succeeded, BakeState.failed, BakeState.aborted)


class StepState(str, Enum):
    running = "running"
    changed = "changed"        # op made a change
    no_change = "no_change"    # idempotent no-op
    failed = "failed"


@dataclass
class BuildSize:
    """VM shape a recipe asks for. Boot fat (``build_memory_megabytes``) for a heavy
    build, then resize down to ``memory_megabytes`` before snapshot; ``0`` = no fattening."""

    vcpus: int = 2
    memory_megabytes: int = 2048
    disk_gigabytes: int = 20
    build_memory_megabytes: int = 0

    @property
    def effective_build_memory_megabytes(self) -> int:
        return self.build_memory_megabytes or self.memory_megabytes


@dataclass
class SshTarget:
    """How the provisioner (pyinfra) reaches the scratch VM, plus the backend handle so
    the Builder can later stop/start/snapshot/release the same VM.

    ``ssh_config_file`` is how AtlasBuilder threads a per-bake ProxyJump config to
    pyinfra's ``@ssh`` connector; for DockerBuilder it's ``None`` (pyinfra ``@docker``)."""

    host: str
    user: str = "root"
    port: int = 22
    key_file: str | None = None
    ssh_config_file: str | None = None
    connector: str = "ssh"        # pyinfra connector: "ssh" | "docker" | "local"
    vm_ref: str = ""              # opaque backend id (Atlas VM name / docker container id)
    extra: dict = field(default_factory=dict)


@dataclass
class HostSignature:
    """Warm images restore only onto a signature-compatible host family. The signature
    comes from the Builder (AtlasBuilder ← the capturing host's facts)."""

    architecture: str = ""
    kernel_version: str = ""
    firecracker_version: str = ""
    jailer_version: str = ""

    def compatible_with(self, other: "HostSignature") -> bool:
        return (
            self.architecture == other.architecture
            and self.kernel_version == other.kernel_version
            and self.firecracker_version == other.firecracker_version
        )

    def as_dict(self) -> dict:
        return {
            "architecture": self.architecture,
            "kernel_version": self.kernel_version,
            "firecracker_version": self.firecracker_version,
            "jailer_version": self.jailer_version,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "HostSignature | None":
        if not data:
            return None
        return cls(**{k: data.get(k, "") for k in ("architecture", "kernel_version",
                                                    "firecracker_version", "jailer_version")})


@dataclass
class SnapshotRef:
    """What ``Builder.snapshot`` returns: where the produced bytes live (backend-scoped)
    plus warm-only provenance."""

    kind: SnapshotKind
    ref: str                                    # backend snapshot id/name/path
    size_bytes: int = 0
    host_signature: HostSignature | None = None  # warm only
    memory_ref: str | None = None                # warm only
    extra: dict = field(default_factory=dict)


@dataclass
class ImageLocation:
    """What ``Publisher.publish`` returns: a destination for the produced image."""

    type: str                    # "local" | "s3" | "atlas-base-image"
    uri: str                     # file://… | s3://… | the base-image name
    manifest: dict = field(default_factory=dict)
