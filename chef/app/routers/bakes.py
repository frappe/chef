"""``/bakes`` — read a bake's status/steps/images, abort it, and (mounted here) stream
its logs over SSE.

A ``BakeStatus`` is assembled from the durable :class:`~chef.store.BakeRecord` plus the
structured step list and produced image ids. Abort targets the arq job whose id is the
bake id (the enqueue side uses ``_job_id=bake_id``).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel

from chef.app import sse
from chef.config import Settings, get_settings
from chef.schemas import BakeStatus, ErrorOut, Links, StepOut
from chef.store import get_bake, images_for_bake, list_steps
from chef.types import BakeState, Mode, StepState

logger = logging.getLogger("chef.bakes")

router = APIRouter()


class AbortResult(BaseModel):
    ok: bool
    status: BakeState
    detail: str = ""


def bake_links(bake_id: str) -> Links:
    """HATEOAS-lite links surfaced on every bake."""
    return Links(
        status=f"/bakes/{bake_id}",
        logs=f"/bakes/{bake_id}/logs",
        abort=f"/bakes/{bake_id}/abort",
    )


@router.get(
    "/{bake_id}",
    response_model=BakeStatus,
    operation_id="get_bake",
    summary="Get a bake's status, steps and images",
    responses={404: {"model": ErrorOut, "description": "No such bake."}},
)
def get_bake_status(bake_id: str = Path(..., description="The bake id.")) -> BakeStatus:
    record = get_bake(bake_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"bake '{bake_id}' not found")
    steps = [
        StepOut(
            index=s.idx,
            name=s.name,
            phase=s.phase,
            state=StepState(s.state),
            retries=s.retries,
        )
        for s in list_steps(bake_id)
    ]
    images = [img.id for img in images_for_bake(bake_id)]
    return BakeStatus(
        id=record.id,
        recipe=record.recipe,
        version=record.version,
        mode=Mode(record.mode),
        builder=record.builder,
        status=BakeState(record.status),
        exit_code=record.exit_code,
        error=record.error,
        steps=steps,
        images=images,
        created_at=record.created_at,
        updated_at=record.updated_at,
        links=bake_links(record.id),
    )


async def _abort_job(bake_id: str, settings: Settings) -> bool:
    """Signal the arq job (id == bake id) to abort. False if redis/worker is unreachable."""
    from arq import create_pool
    from arq.connections import RedisSettings
    from arq.jobs import Job

    try:
        pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    except Exception:  # noqa: BLE001 - redis down: report, don't 500
        logger.warning("abort: cannot reach redis for bake %s", bake_id, exc_info=True)
        return False
    try:
        return await Job(bake_id, pool).abort(timeout=2)
    except Exception:  # noqa: BLE001
        logger.warning("abort: failed to signal bake %s", bake_id, exc_info=True)
        return False
    finally:
        try:
            await pool.aclose()
        except Exception:  # noqa: BLE001
            pass


@router.post(
    "/{bake_id}/abort",
    response_model=AbortResult,
    operation_id="abort_bake",
    summary="Abort a running bake",
    responses={404: {"model": ErrorOut, "description": "No such bake."}},
)
async def abort_bake(
    bake_id: str = Path(..., description="The bake id."),
    settings: Settings = Depends(get_settings),
) -> AbortResult:
    record = get_bake(bake_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"bake '{bake_id}' not found")
    status = BakeState(record.status)
    if status.terminal:
        return AbortResult(ok=False, status=status, detail=f"bake already {status.value}")
    signalled = await _abort_job(bake_id, settings)
    detail = "abort signalled" if signalled else "could not signal job (redis/worker down?)"
    return AbortResult(ok=signalled, status=status, detail=detail)


# Mount the SSE log stream (GET /bakes/{bake_id}/logs) — see app/sse.py.
router.include_router(sse.router)
