"""Server-sent bake logs.

``GET /bakes/{id}/logs`` replays the whole Redis Stream ``chef:bake:{id}:log`` from the
beginning, then tails it with a blocking ``XREAD`` for new entries, reframing each stream
entry (whose one field carries a JSON event dict) as an ``EventSource`` message:

    event: <dict["type"]>
    data:  <compact json of the dict>

The generator stops after a ``done`` event (terminal) and on client disconnect. This
matches Pilot's lifted ``useTaskStream.js`` reader (``line``/``overwrite``/``status``/
``step``/``done``).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Path, Request
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from chef.config import get_settings

router = APIRouter()

# How long a single blocking XREAD parks before we loop to re-check disconnect (ms).
_BLOCK_MS = 15_000


def log_stream_key(bake_id: str) -> str:
    """The Redis Stream key the worker XADDs bake events onto."""
    return f"chef:bake:{bake_id}:log"


def _decode(value: Any) -> str:
    return value.decode() if isinstance(value, (bytes, bytearray)) else value


def _entry_to_event(fields: dict) -> dict[str, Any] | None:
    """Pull the JSON event dict out of one stream entry's fields.

    The worker XADDs a single field per entry; we accept whatever key it used
    (``data``/``event``/``json`` or the first field) and tolerate a raw string.
    """
    if not fields:
        return None
    decoded = {_decode(k): v for k, v in fields.items()}
    raw = None
    for key in ("data", "event", "json"):
        if key in decoded:
            raw = decoded[key]
            break
    if raw is None:
        raw = next(iter(decoded.values()))
    payload = _decode(raw)
    try:
        event = json.loads(payload)
    except (ValueError, TypeError):
        return {"type": "line", "line": payload}
    if not isinstance(event, dict):
        return {"type": "line", "line": str(event)}
    return event


def _sse(event: dict[str, Any]) -> ServerSentEvent:
    return ServerSentEvent(
        data=json.dumps(event, separators=(",", ":")),
        event=str(event.get("type", "message")),
    )


async def _iter_bake_events(request: Request, bake_id: str) -> AsyncIterator[ServerSentEvent]:
    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url)
    key = log_stream_key(bake_id)
    last_id = "0"
    try:
        # 1) Replay everything already recorded (the client may attach mid-bake).
        for entry_id, fields in await redis.xrange(key, min="-", max="+"):
            last_id = _decode(entry_id)
            event = _entry_to_event(fields)
            if event is None:
                continue
            yield _sse(event)
            if event.get("type") == "done":
                return
        # 2) Tail for new entries until the terminal `done` or the client leaves.
        while not await request.is_disconnected():
            batches = await redis.xread({key: last_id}, block=_BLOCK_MS, count=200)
            if not batches:
                continue  # block timed out with no data — loop re-checks disconnect
            for _stream, entries in batches:
                for entry_id, fields in entries:
                    last_id = _decode(entry_id)
                    event = _entry_to_event(fields)
                    if event is None:
                        continue
                    yield _sse(event)
                    if event.get("type") == "done":
                        return
    finally:
        try:
            await redis.aclose()
        except Exception:  # noqa: BLE001 - best-effort cleanup on disconnect/shutdown
            pass


@router.get(
    "/{bake_id}/logs",
    operation_id="stream_bake_logs",
    summary="Stream a bake's logs (SSE)",
    tags=["bakes"],
    response_class=EventSourceResponse,
    responses={
        200: {
            "description": "An `text/event-stream` of bake events "
            "(`line`/`overwrite`/`step`/`status`/`done`).",
            "content": {"text/event-stream": {}},
        }
    },
)
async def stream_bake_logs(
    request: Request,
    bake_id: str = Path(..., description="The bake id."),
) -> EventSourceResponse:
    """Replay + tail the bake's Redis log stream as SSE. Closes after `done`."""
    return EventSourceResponse(_iter_bake_events(request, bake_id))
