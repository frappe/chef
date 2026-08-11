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


async def enqueue_bake(bake_id: str, redis_url: str | None = None) -> bool:
    """Enqueue the arq ``bake`` task with ``_job_id == bake_id`` (so it can be aborted
    later). Returns ``False`` if redis is unreachable — the caller keeps the durable bake
    row regardless. The single enqueue path shared by the API (``POST /recipes/{name}/bake``)
    and the CLI (``chef bake --async``), so a UI bake and a CLI bake run on the *same* worker."""
    from arq import create_pool

    try:
        pool = await create_pool(redis_settings_from_url(redis_url))
    except Exception:  # noqa: BLE001 - redis down: report, don't raise
        return False
    try:
        await pool.enqueue_job("bake", bake_id, _job_id=bake_id)
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            await pool.aclose()
        except Exception:  # noqa: BLE001
            pass


async def on_startup(ctx: dict) -> None:
    store.init_db()


class WorkerSettings:
    functions = [bake]
    redis_settings = redis_settings_from_url()
    on_startup = on_startup
