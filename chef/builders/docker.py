"""DockerBuilder — a fleet-free Builder backed by local Docker containers.

Recipes can be authored and baked against a throwaway container instead of a real VM.
We prefer pyinfra's ``@docker`` connector (``connector="docker"``), so the scratch
container needs **no sshd**: pyinfra ``docker exec``s into it. The Builder itself only
shells out to the ``docker`` CLI (``subprocess``) — chef core never imports a Docker SDK.

``acquire`` runs a detached, ssh-less ubuntu-ish container kept alive with ``sleep
infinity``; ``snapshot`` = ``docker commit`` + ``docker save`` to a tar under the data dir;
``stop``/``start`` map to ``docker stop``/``docker start``; ``release`` = ``docker rm -f``.
Docker has no meaningful host family, so ``host_signature`` stays ``None`` (base default).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from chef.builders import BuilderError
from chef.builders.base import Builder
from chef.types import BuildSize, SnapshotKind, SnapshotRef, SshTarget

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: explicit chef-name → docker-image overrides; anything else falls through the heuristic.
_IMAGE_MAP = {
    "ubuntu-24.04": "ubuntu:24.04",
    "ubuntu-22.04": "ubuntu:22.04",
    "ubuntu-20.04": "ubuntu:20.04",
    "debian-12": "debian:12",
    "debian-11": "debian:11",
}

_SLUG_RE = re.compile(r"[^a-z0-9._-]+")


def _slug(value: str) -> str:
    return _SLUG_RE.sub("-", (value or "image").lower()).strip("-") or "image"


def _docker_image(base_image: str) -> str:
    """Map a chef ``base_image`` name (``ubuntu-24.04``) to a docker ref (``ubuntu:24.04``)."""
    base_image = base_image or "ubuntu-24.04"
    if base_image in _IMAGE_MAP:
        return _IMAGE_MAP[base_image]
    if ":" in base_image or "/" in base_image:
        return base_image  # already a docker ref
    name, sep, tag = base_image.partition("-")
    return f"{name}:{tag}" if sep else base_image


class DockerBuilder(Builder):
    """A :class:`~chef.builders.base.Builder` backed by local Docker containers."""

    name = "docker"

    def __init__(self, data_dir: Path | None = None):
        self.data_dir = Path(data_dir) if data_dir else _REPO_ROOT / "data" / "snapshots"

    # --- helpers --------------------------------------------------------------

    def _docker(self, *args: str, check: bool = True) -> str:
        try:
            proc = subprocess.run(
                ["docker", *args], capture_output=True, text=True, check=False
            )
        except FileNotFoundError as exc:
            raise BuilderError(
                "docker CLI not found on PATH — install Docker or pick a different builder"
            ) from exc
        if check and proc.returncode != 0:
            raise BuilderError(
                f"`docker {' '.join(args)}` failed (exit {proc.returncode}): "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
        return proc.stdout

    # --- lifecycle ------------------------------------------------------------

    def acquire(self, base_image: str, size: BuildSize, *, title: str) -> SshTarget:  # noqa: ARG002
        image = _docker_image(base_image)
        container_id = self._docker(
            "run", "-d", "--label", f"chef.title={_slug(title)}",
            image, "sleep", "infinity",
        ).strip()
        if not container_id:
            raise BuilderError(f"docker run returned no container id for image {image!r}")
        return SshTarget(connector="docker", host=container_id, vm_ref=container_id)

    def stop(self, target: SshTarget) -> None:
        self._docker("stop", target.vm_ref)

    def start(self, target: SshTarget) -> None:
        self._docker("start", target.vm_ref)

    def snapshot(self, target: SshTarget, kind: SnapshotKind, *, title: str) -> SnapshotRef:
        kind = SnapshotKind(kind)
        short = target.vm_ref[:8] or "unknown"
        slug = _slug(title)
        tag = f"chef/{slug}:{short}"
        self._docker("commit", target.vm_ref, tag)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tar_path = self.data_dir / f"{slug}-{kind.value}-{short}.tar"
        self._docker("save", "-o", str(tar_path), tag)
        size_bytes = tar_path.stat().st_size if tar_path.exists() else 0
        return SnapshotRef(
            kind=kind,
            ref=str(tar_path),
            size_bytes=size_bytes,
            extra={"docker_image": tag},
        )

    def release(self, target: SshTarget) -> None:
        if not target.vm_ref:
            return
        # best-effort: releasing an already-gone container must not raise.
        self._docker("rm", "-f", target.vm_ref, check=False)
