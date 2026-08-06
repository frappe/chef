"""LocalBuilder — the no-op Builder for authoring and unit tests.

Runs pyinfra's ``@local`` connector against the machine chef itself runs on, so recipes can
be exercised with zero fleet and zero Docker. It never captures real bytes: ``snapshot``
returns a stub ``local://<title>`` ref, and stop/start/release are no-ops. Useful for the
recipe write→validate loop and for hermetic tests of the worker pipeline.
"""

from __future__ import annotations

from chef.builders.base import Builder
from chef.types import BuildSize, SnapshotKind, SnapshotRef, SshTarget


class LocalBuilder(Builder):
    name = "local"

    def acquire(self, base_image: str, size: BuildSize, *, title: str) -> SshTarget:  # noqa: ARG002
        return SshTarget(connector="local", host="@local", vm_ref="local")

    def snapshot(self, target: SshTarget, kind: SnapshotKind, *, title: str) -> SnapshotRef:  # noqa: ARG002
        kind = SnapshotKind(kind)
        return SnapshotRef(kind=kind, ref=f"local://{title}")

    def release(self, target: SshTarget) -> None:
        """No scratch VM to tear down."""
