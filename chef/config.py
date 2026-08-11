"""Runtime configuration, sourced from the environment (prefix ``CHEF_``).

Every knob has a dev-friendly default so ``docker compose up`` and a bare
``chef serve`` both work with zero config. Production overrides via env vars or a
``.env`` file.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CHEF_", env_file=".env", extra="ignore"
    )

    # --- API / auth ---
    api_token: str = Field(default="chef-dev-token", description="Static bearer token (v1 auth).")
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # --- jobs / streaming ---
    redis_url: str = "redis://localhost:6379"
    log_stream_ttl_seconds: int = 60 * 60 * 24  # Redis Streams log retention
    # arq's default job_timeout is 300s — far too short for a fleet bake (nginx ≈ minutes,
    # pilot ≈ 40 min), which would otherwise be cancelled mid-build and recorded `aborted`.
    bake_job_timeout: int = 60 * 60 * 3  # 3h ceiling for one bake on the worker

    # --- data store ---
    database_url: str = f"sqlite:///{_REPO_ROOT / 'chef.db'}"

    # --- recipes ---
    recipes_dir: Path = _REPO_ROOT / "recipes"

    # --- builder / publisher selection ---
    default_builder: str = "docker"  # docker | local | atlas

    # --- S3 / object store (MinIO in dev) ---
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str = "chef-images"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_prefix: str = "images"

    # --- Atlas backend (AtlasBuilder / AtlasPublisher) ---
    atlas_url: str | None = None          # e.g. http://atlas.localhost:8000
    atlas_api_key: str | None = None
    atlas_api_secret: str | None = None
    atlas_ssh_key_file: str | None = None  # chef's private key; its pubkey is in service_public_keys
    atlas_server: str | None = None        # pin bakes to a specific Atlas Server (else placement picks)

    @property
    def s3_configured(self) -> bool:
        return bool(self.s3_access_key and self.s3_secret_key)

    @property
    def atlas_configured(self) -> bool:
        return bool(self.atlas_url and self.atlas_api_key and self.atlas_api_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
