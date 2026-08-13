"""The bake pipeline — the one place that drives a Builder + Publishers through a recipe.

The pipeline core :func:`run_pipeline` is **synchronous** and must run on a process's
*main thread*: pyinfra runs on gevent, whose child watchers only exist on the default
(main-thread) hub, so running a phase in a worker thread fails with
``child watchers are only available on the default loop``. Two callers respect that:

  * ``run_bake_inline(bake_id)`` (CLI / tests) runs it directly on the caller's main
    thread with an in-memory sink — no arq, no Redis.
  * ``bake(ctx, bake_id)`` (the arq task) runs it in an isolated **subprocess**
    (``python -m chef.worker.entry <bake_id>``), which owns *its* main thread. That also
    makes ``Job.abort`` a clean process-kill, and keeps one heavy gevent world per bake.

Events (``chef.events`` dicts) are handed to an ``emit`` callable: the subprocess/CLI wire
it to Redis (``XADD`` onto ``chef:bake:{id}:log``) or to a list. Structured ``step`` events
are additionally mirrored into ``store.record_step`` for a durable step list.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from collections.abc import Callable

import redis

from chef import store
from chef.builders import get_builder
from chef.config import get_settings
from chef.engine.pyinfra_runner import run_phase
from chef.engine.recipe import Recipe, load_recipe
from chef.events import done_event, line_event, status_event
from chef.publishers import get_publisher
from chef.releases import resolve_ref
from chef.types import BakeState, Mode, SnapshotKind, SnapshotRef

Emit = Callable[[dict], None]


def _resolve_releases(recipe: Recipe, bake: store.BakeRecord) -> dict[str, dict]:
    """Resolve every repo the recipe tracks to its pinned ``{ref, sha}``.

    The effective ref is the per-bake override (``bake.releases``) if given, else the store
    pin. **Fail-closed**: an unpinned tracked repo (or an unresolvable ref) raises, so the
    bake stops before a VM is acquired rather than baking a surprise version.
    """
    out: dict[str, dict] = {}
    overrides = bake.releases or {}
    for repo in recipe.tracked_repos():
        ref = overrides.get(repo)
        if not ref:
            pin = store.get_pin(repo)
            ref = pin.ref if pin else None
        if not ref:
            raise ValueError(
                f"recipe '{bake.recipe}' tracks '{repo}' but no release is pinned — "
                f"set one with `chef releases set {repo} <ref>` or PUT /releases"
            )
        sha = resolve_ref(repo, ref)
        if not sha:
            raise ValueError(f"release ref '{ref}' not found in '{repo}'")
        out[repo] = {"ref": ref, "sha": sha}
    return out


# --- the shared, synchronous pipeline ----------------------------------------


def run_pipeline(bake_id: str, emit: Emit) -> int:
    """Drive one bake from acquire → publish, emitting events and persisting state.

    Returns the exit code (0 success, 1 failure/abort). Runs entirely on the calling
    thread — the caller MUST be a process main thread (pyinfra/gevent requirement).

    Terminal handling: success → ``done(0)``; any exception → ``line`` + ``done(1)`` +
    a ``failed`` record; ``KeyboardInterrupt``/``SystemExit`` (subprocess SIGTERM = abort)
    → ``aborted``. The builder is always released best-effort.
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
        # --- resolve tracked releases (fail-closed, before acquiring a VM) ----
        releases = _resolve_releases(recipe, bake)
        for repo, pin in releases.items():
            emit(line_event(f"release {repo} @ {pin['ref']} ({pin['sha'][:12]})"))

        # --- acquire ---------------------------------------------------------
        _set(BakeState.acquiring)
        title = f"{recipe.manifest.name}-{version}"
        target = builder.acquire(recipe.manifest.base_image, recipe.manifest.size, title=title)
        store.set_bake(bake_id, vm_ref=target.vm_ref)

        # --- build -----------------------------------------------------------
        _set(BakeState.building, phase="build")
        run_phase(target, recipe, "build", inputs, _phase_emit("build"), releases=releases)

        # --- verify (fail-loud gate before any snapshot) ---------------------
        if recipe.has_phase("verify"):
            _set(BakeState.verifying, phase="verify")
            run_phase(target, recipe, "verify", inputs, _phase_emit("verify"), releases=releases)

        # --- snapshot (cold before warm, decision #7) ------------------------
        _set(BakeState.snapshotting)
        snapshots: dict[SnapshotKind, SnapshotRef] = {}
        for kind in Mode(bake.mode).kinds():
            if kind is SnapshotKind.cold:
                builder.stop(target)
                snapshots[kind] = builder.snapshot(
                    target, SnapshotKind.cold, title=f"{title}-cold"
                )
            elif kind is SnapshotKind.warm:
                builder.start(target)
                if recipe.has_phase("warm_arm"):
                    run_phase(target, recipe, "warm_arm", inputs, _phase_emit("warm_arm"),
                              releases=releases)
                snapshots[kind] = builder.snapshot(
                    target, SnapshotKind.warm, title=f"{title}-warm"
                )

        # --- publish ---------------------------------------------------------
        _set(BakeState.publishing)
        for kind, snap in snapshots.items():
            for pub_cfg in recipe.manifest.publish:
                publisher = get_publisher(pub_cfg["type"])
                if publisher.builders and builder.name not in publisher.builders:
                    emit(line_event(
                        f"skip publish '{pub_cfg['type']}' — needs builder "
                        f"{'/'.join(publisher.builders)}, active is '{builder.name}'"
                    ))
                    continue
                loc = publisher.publish(
                    snap, recipe=recipe.manifest.name, version=version, config=pub_cfg
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
                        provenance={"builder": builder.name, "inputs": inputs,
                                    "releases": releases},
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
        _release(builder, target)
        target = None
        store.set_bake(bake_id, status=BakeState.succeeded.value, exit_code=0)
        emit(done_event(0, BakeState.succeeded.value))
        return 0

    except (KeyboardInterrupt, SystemExit):
        store.set_bake(bake_id, status=BakeState.aborted.value, exit_code=1)
        emit(line_event("bake aborted"))
        emit(done_event(1, BakeState.aborted.value))
        _release(builder, target)
        return 1
    except Exception as exc:  # noqa: BLE001 - record the failure, don't crash the worker
        emit(line_event(str(exc)))
        store.set_bake(bake_id, status=BakeState.failed.value, exit_code=1, error=str(exc))
        emit(done_event(1, BakeState.failed.value))
        _release(builder, target)
        return 1


def _release(builder, target) -> None:
    """Best-effort teardown; a release failure must never mask the bake's own outcome."""
    if target is None:
        return
    try:
        builder.release(target)
    except Exception:  # noqa: BLE001
        pass


# --- redis-backed emit -------------------------------------------------------


def redis_emit(bake_id: str, redis_url: str, ttl_seconds: int) -> Emit:
    """An ``emit`` that ``XADD``s each event (JSON under a ``data`` field) onto the per-bake
    Redis Stream and refreshes its TTL. Synchronous client — safe on a pipeline main thread."""
    client = redis.Redis.from_url(redis_url)
    key = f"chef:bake:{bake_id}:log"

    def emit(event: dict) -> None:
        client.xadd(key, {"data": json.dumps(event)})
        if ttl_seconds:
            client.expire(key, ttl_seconds)

    return emit


# --- entry points ------------------------------------------------------------


async def bake(ctx: dict, bake_id: str) -> int:
    """arq task: run the pipeline in an isolated subprocess so pyinfra owns a main thread.

    The subprocess streams events straight to Redis; abort (``Job.abort`` →
    ``CancelledError``) terminates it (SIGTERM → the subprocess records ``aborted``)."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "chef.worker.entry", bake_id
    )
    try:
        return await proc.wait()
    except asyncio.CancelledError:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
        raise


def run_bake_inline(bake_id: str) -> list[dict]:
    """Run the pipeline synchronously with an in-memory sink; return the events.

    Used by the CLI (``chef bake``) and tests — no arq, no Redis. Runs on the caller's
    main thread. The bake record must already exist in the store."""
    store.init_db()
    events: list[dict] = []
    run_pipeline(bake_id, events.append)
    return events
