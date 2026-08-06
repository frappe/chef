"""The bake pipeline — the one place that drives a Builder + Publishers through a recipe.

``bake(ctx, bake_id)`` is the arq task; ``run_bake_inline(bake_id)`` runs the *same*
pipeline synchronously with an in-memory event sink (for the CLI and tests — no arq, no
Redis). Both call the shared coroutine :func:`_run_pipeline`.

Builders, publishers and ``run_phase`` are all **synchronous** and can block for minutes,
so every one of those calls is dispatched through :func:`asyncio.to_thread`, keeping the
event loop free (and giving arq's ``Job.abort`` a cancellation point at each ``await``).

Events (``chef.events`` dicts) are handed to an ``emit`` callable:

  * the arq task's ``emit`` ``XADD``s each event onto Redis Stream ``chef:bake:{id}:log``
    (a plain synchronous redis client — safe to call from the worker threads);
  * the inline helper's ``emit`` just appends to a list.

Structured ``step`` events are additionally mirrored into ``store.record_step`` so the
durable step list survives past the Redis stream's TTL.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable

import redis

from chef.builders import get_builder
from chef.config import get_settings
from chef.engine.recipe import load_recipe
from chef.events import done_event, line_event, status_event
from chef.publishers import get_publisher
from chef.types import BakeState, Mode, SnapshotKind
from chef import store

# ``chef.engine.pyinfra_runner`` is written by a sibling module and may not exist yet.
# Import it if present; otherwise fall back to a placeholder that fails loudly *when
# called* — this keeps ``import chef.worker.bake_job`` total, and tests monkeypatch the
# module-level ``run_phase`` name regardless.
try:
    from chef.engine.pyinfra_runner import run_phase
except ImportError:  # pragma: no cover - exercised only until the engine lands

    def run_phase(target, recipe, phase, inputs, emit):  # type: ignore[misc]
        raise RuntimeError(
            "chef.engine.pyinfra_runner.run_phase is not available yet"
        )


Emit = Callable[[dict], None]


# --- the shared pipeline -----------------------------------------------------


async def _run_pipeline(bake_id: str, emit: Emit) -> None:
    """Drive one bake from acquire → publish, emitting events and persisting state.

    Terminal handling: success → ``done(0)``; any exception → a ``line`` + ``done(1)`` and
    a ``failed`` record (swallowed, not re-raised — the run is *recorded*, not crashed);
    ``CancelledError`` (arq abort) → ``aborted`` + release + re-raise. The builder is
    always released best-effort.
    """
    settings = get_settings()

    bake = store.get_bake(bake_id)
    if bake is None:
        raise ValueError(f"bake {bake_id!r} not found")

    recipe = load_recipe(settings.recipes_dir, bake.recipe)
    inputs = recipe.validate_inputs(bake.inputs)
    version = bake.version or recipe.manifest.version
    builder = get_builder(bake.builder)

    def _set(state: BakeState, phase: str | None = None, **extra) -> None:
        store.set_bake(bake_id, status=state.value, **extra)
        emit(status_event(state.value, phase))

    def _phase_emit(phase: str) -> Emit:
        """Wrap ``emit`` so ``step`` events during ``phase`` are mirrored into the store."""

        def _emit(event: dict) -> None:
            emit(event)
            if event.get("type") == "step":
                store.record_step(
                    bake_id,
                    idx=event["index"],
                    name=event["name"],
                    phase=phase,
                    state=event["state"],
                    retries=event.get("retries", 0),
                )

        return _emit

    target = None
    try:
        # --- acquire ---------------------------------------------------------
        _set(BakeState.acquiring)
        title = f"{recipe.manifest.name}-{version}"
        target = await asyncio.to_thread(
            builder.acquire, recipe.manifest.base_image, recipe.manifest.size, title=title
        )
        store.set_bake(bake_id, vm_ref=target.vm_ref)

        # --- build -----------------------------------------------------------
        _set(BakeState.building, phase="build")
        await asyncio.to_thread(
            run_phase, target, recipe, "build", inputs, _phase_emit("build")
        )

        # --- verify (fail-loud gate before any snapshot) ---------------------
        if recipe.has_phase("verify"):
            _set(BakeState.verifying, phase="verify")
            await asyncio.to_thread(
                run_phase, target, recipe, "verify", inputs, _phase_emit("verify")
            )

        # --- snapshot (cold before warm, decision #7) ------------------------
        _set(BakeState.snapshotting)
        snapshots: dict[SnapshotKind, object] = {}
        for kind in Mode(bake.mode).kinds():
            if kind is SnapshotKind.cold:
                await asyncio.to_thread(builder.stop, target)
                snapshots[kind] = await asyncio.to_thread(
                    builder.snapshot, target, SnapshotKind.cold,
                    title=f"{recipe.manifest.name}-{version}-cold",
                )
            elif kind is SnapshotKind.warm:
                await asyncio.to_thread(builder.start, target)
                if recipe.has_phase("warm_arm"):
                    await asyncio.to_thread(
                        run_phase, target, recipe, "warm_arm", inputs, _phase_emit("warm_arm")
                    )
                snapshots[kind] = await asyncio.to_thread(
                    builder.snapshot, target, SnapshotKind.warm,
                    title=f"{recipe.manifest.name}-{version}-warm",
                )

        # --- publish ---------------------------------------------------------
        _set(BakeState.publishing)
        for kind, snap in snapshots.items():
            for pub_cfg in recipe.manifest.publish:
                publisher = get_publisher(pub_cfg["type"])
                loc = await asyncio.to_thread(
                    publisher.publish, snap,
                    recipe=recipe.manifest.name, version=version, config=pub_cfg,
                )
                emit(line_event(f"published {kind.value} → {loc.uri}"))
                store.create_image(
                    store.ImageRecord(
                        id=uuid.uuid4().hex,
                        bake_id=bake_id,
                        recipe=recipe.manifest.name,
                        version=version,
                        kind=kind.value,
                        base_image=recipe.manifest.base_image,
                        provenance={"builder": builder.name, "inputs": inputs},
                        location_type=loc.type,
                        location_uri=loc.uri,
                        manifest=loc.manifest,
                        size_bytes=snap.size_bytes,
                        host_signature=(
                            snap.host_signature.as_dict() if snap.host_signature else None
                        ),
                    )
                )

        # --- done ------------------------------------------------------------
        await _release(builder, target)
        target = None
        store.set_bake(bake_id, status=BakeState.succeeded.value, exit_code=0)
        emit(done_event(0, BakeState.succeeded.value))

    except asyncio.CancelledError:
        store.set_bake(bake_id, status=BakeState.aborted.value, exit_code=1)
        emit(line_event("bake aborted"))
        emit(done_event(1, BakeState.aborted.value))
        await _release(builder, target)
        raise
    except Exception as exc:  # noqa: BLE001 - record the failure, don't crash the worker
        emit(line_event(str(exc)))
        store.set_bake(bake_id, status=BakeState.failed.value, exit_code=1, error=str(exc))
        emit(done_event(1, BakeState.failed.value))
        await _release(builder, target)


async def _release(builder, target) -> None:
    """Best-effort teardown; a release failure must never mask the bake's own outcome."""
    if target is None:
        return
    try:
        await asyncio.to_thread(builder.release, target)
    except Exception:  # noqa: BLE001
        pass


# --- redis-backed emit -------------------------------------------------------


def _redis_emit(bake_id: str, redis_url: str, ttl_seconds: int) -> Emit:
    """An ``emit`` that ``XADD``s each event (as JSON under a ``data`` field) onto the
    per-bake Redis Stream and refreshes its TTL. Uses a synchronous client so it is safe
    to call from the worker threads that run the sync builder/publisher/run_phase code."""
    client = redis.Redis.from_url(redis_url)
    key = f"chef:bake:{bake_id}:log"

    def emit(event: dict) -> None:
        client.xadd(key, {"data": json.dumps(event)})
        if ttl_seconds:
            client.expire(key, ttl_seconds)

    return emit


# --- entry points ------------------------------------------------------------


async def bake(ctx: dict, bake_id: str) -> None:
    """arq task: run the pipeline, streaming events onto the bake's Redis Stream."""
    settings = get_settings()
    emit = _redis_emit(bake_id, settings.redis_url, settings.log_stream_ttl_seconds)
    await _run_pipeline(bake_id, emit)


def run_bake_inline(bake_id: str) -> list[dict]:
    """Run the same pipeline synchronously with an in-memory sink; return the events.

    Used by the CLI (``chef bake --inline``) and tests — no arq, no Redis. The bake record
    must already exist in the store."""
    store.init_db()
    events: list[dict] = []
    asyncio.run(_run_pipeline(bake_id, events.append))
    return events
