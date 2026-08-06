"""The streamed-event wire contract.

Every event is a small JSON object with a ``type`` discriminator. The shapes mirror
Pilot's SSE reader (``line``/``overwrite``/``status``/``done``) so the lifted frontend
composable works unchanged, plus a structured ``step`` event for per-operation results.

The worker publishes these dicts onto a Redis Stream (one stream per bake); the SSE
endpoint replays + tails that stream and reframes each as an ``EventSource`` message.
"""

from __future__ import annotations

from typing import Any

from chef.types import StepState


def line_event(line: str) -> dict[str, Any]:
    """A completed output line."""
    return {"type": "line", "line": line}


def overwrite_event(line: str) -> dict[str, Any]:
    """A trailing partial line (progress-bar style) that replaces the previous one."""
    return {"type": "overwrite", "line": line}


def step_event(name: str, index: int, total: int, state: StepState, retries: int = 0) -> dict[str, Any]:
    """A structured per-operation result (name, changed?, retries)."""
    return {
        "type": "step",
        "name": name,
        "index": index,
        "total": total,
        "state": StepState(state).value,
        "retries": retries,
    }


def status_event(status: str, phase: str | None = None) -> dict[str, Any]:
    """A bake-state transition (queued → acquiring → building → …)."""
    return {"type": "status", "status": status, "phase": phase}


def done_event(exit_code: int, status: str) -> dict[str, Any]:
    """Terminal event. ``exit_code == 0`` is the success signal (Pilot contract)."""
    return {"type": "done", "exit_code": exit_code, "status": status}
