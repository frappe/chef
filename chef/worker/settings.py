"""arq worker configuration.

``chef worker`` runs ``arq chef.worker.settings.WorkerSettings``. The worker registers the
single :func:`chef.worker.bake_job.bake` task, points at Redis from ``settings.redis_url``,
and ensures the SQLite schema exists on startup.
"""

from __future__ import annotations

from arq.connections import RedisSettings

from chef.config import get_settings
from chef.worker.bake_job import bake
from chef import store


def redis_settings_from_url(url: str | None = None) -> RedisSettings:
    """Build arq :class:`RedisSettings` from a ``redis://`` URL (defaults to config)."""
    return RedisSettings.from_dsn(url or get_settings().redis_url)


async def on_startup(ctx: dict) -> None:
    store.init_db()


class WorkerSettings:
    functions = [bake]
    redis_settings = redis_settings_from_url()
    on_startup = on_startup
