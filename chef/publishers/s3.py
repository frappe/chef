"""S3Publisher — upload an image's bytes to an S3-compatible object store.

Works against AWS S3 or a MinIO dev endpoint (``settings.s3_endpoint_url``). The boto3
client is built lazily inside :meth:`publish` from :mod:`chef.config`, so importing this
module never touches boto3 or credentials. A ``[[publish]]`` block may override ``bucket``
and ``prefix``; everything else comes from settings.
"""

from __future__ import annotations

from pathlib import Path

from chef.config import Settings, get_settings
from chef.publishers import PublisherError
from chef.publishers.base import Publisher
from chef.types import ImageLocation, SnapshotRef


class S3Publisher(Publisher):
    type = "s3"
    builders = ("docker", "local")  # uploads a local snapshot tar

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def _client(self):
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - boto3 is a hard dep
            raise PublisherError("boto3 is not installed") from exc
        s = self.settings
        return boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url,
            region_name=s.s3_region,
            aws_access_key_id=s.s3_access_key,
            aws_secret_access_key=s.s3_secret_key,
        )

    def publish(
        self,
        snapshot: SnapshotRef,
        *,
        recipe: str,
        version: str,
        config: dict,
    ) -> ImageLocation:
        s = self.settings
        if not s.s3_configured:
            raise PublisherError(
                "S3 is not configured — set CHEF_S3_ACCESS_KEY and CHEF_S3_SECRET_KEY"
            )
        src = Path(snapshot.ref)
        if not src.is_file():
            raise PublisherError(
                f"cannot publish: snapshot ref {snapshot.ref!r} is not a readable file"
            )
        bucket = config.get("bucket") or s.s3_bucket
        prefix = config.get("prefix") or s.s3_prefix
        key = f"{prefix.rstrip('/')}/{recipe}/{version}/{src.name}"

        self._client().upload_file(str(src), bucket, key)

        return ImageLocation(
            type="s3",
            uri=f"s3://{bucket}/{key}",
            manifest={
                "bucket": bucket,
                "key": key,
                "endpoint_url": s.s3_endpoint_url,
                "region": s.s3_region,
                "kind": snapshot.kind.value,
                "size_bytes": snapshot.size_bytes or src.stat().st_size,
            },
        )
