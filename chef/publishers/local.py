"""LocalPublisher — copy an image's bytes into a local images directory.

The dev/authoring sink: it hardlinks (falling back to a copy across filesystems) the
snapshot tar into ``<repo>/data/images/<recipe>/<version>/`` and hands back a ``file://``
:class:`~chef.types.ImageLocation`. No object store, no fleet.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from chef.publishers import PublisherError
from chef.publishers.base import Publisher
from chef.types import ImageLocation, SnapshotRef

_REPO_ROOT = Path(__file__).resolve().parents[2]


class LocalPublisher(Publisher):
    type = "local"

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = Path(base_dir) if base_dir else _REPO_ROOT / "data" / "images"

    def publish(
        self,
        snapshot: SnapshotRef,
        *,
        recipe: str,
        version: str,
        config: dict,  # noqa: ARG002 - no local-specific knobs
    ) -> ImageLocation:
        src = Path(snapshot.ref)
        if not src.is_file():
            raise PublisherError(
                f"cannot publish: snapshot ref {snapshot.ref!r} is not a readable file"
            )
        dest_dir = self.base_dir / recipe / version
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        if dest.exists():
            dest.unlink()
        try:
            os.link(src, dest)  # cheap hardlink when on the same filesystem
        except OSError:
            shutil.copy2(src, dest)
        return ImageLocation(
            type="local",
            uri=f"file://{dest}",
            manifest={
                "path": str(dest),
                "recipe": recipe,
                "version": version,
                "kind": snapshot.kind.value,
                "size_bytes": snapshot.size_bytes or dest.stat().st_size,
                "source": str(src),
            },
        )
