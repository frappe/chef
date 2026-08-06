"""Subprocess entry point: run one bake's pipeline on *this* process's main thread.

Invoked as ``python -m chef.worker.entry <bake_id>`` by the arq ``bake`` task, so
pyinfra/gevent get a real main thread (their child watchers require it). Events stream
straight to the bake's Redis stream. SIGTERM — the arq abort path (``Job.abort`` →
subprocess ``terminate()``) — is turned into ``KeyboardInterrupt`` so the pipeline records
``aborted`` and releases the builder before exiting.
"""

from __future__ import annotations

import signal
import sys

from chef.config import get_settings
from chef.store import init_db
from chef.worker.bake_job import redis_emit, run_pipeline


def _on_sigterm(signum, frame) -> None:  # noqa: ARG001 - signal handler signature
    raise KeyboardInterrupt


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python -m chef.worker.entry <bake_id>", file=sys.stderr)
        return 2
    bake_id = sys.argv[1]
    signal.signal(signal.SIGTERM, _on_sigterm)
    settings = get_settings()
    init_db()
    emit = redis_emit(bake_id, settings.redis_url, settings.log_stream_ttl_seconds)
    return run_pipeline(bake_id, emit)


if __name__ == "__main__":
    raise SystemExit(main())
