"""Drive a recipe phase through pyinfra and stream its per-operation results.

This is the seam between chef's recipes (pure pyinfra ``@deploy`` callables) and the
worker: it builds a one-host pyinfra inventory from an :class:`SshTarget`, enqueues the
phase's operations, then runs them **one at a time** so every operation turns into a
structured ``step`` event plus ``line``/``overwrite`` events for its output. Any failed
operation is fatal — it raises, which is how a failing ``verify`` phase aborts a bake
before it snapshots.

Pinned to the pyinfra **3.x** programmatic API (developed against 3.10). The exact calls:

  * ``Inventory((names_data, {}))`` — host name carries ``inputs`` as host data, so
    recipes read ``host.data.get("key")``.
  * ``State(inventory, Config(), check_for_changes=False)`` + ``connect_all(state)``.
  * ``add_deploy(state, phase_callable)`` — enqueues the phase's ops onto the state.
  * ``state.get_op_order()`` — the ordered op hashes; per op:
    ``run_host_op(state, host, op_hash)`` under ``ctx_state``/``ctx_host``, then read the
    op's :class:`OperationMeta` (``did_succeed`` / ``did_change`` / ``retry_attempts`` /
    ``stdout_lines`` / ``stderr_lines``).

Streaming path in use: **PRIMARY (per-operation)**. Because a recipe phase is a single
``@deploy`` that queues N ops, we enqueue the whole phase once (``add_deploy``) and then
execute the queued ops individually via ``run_host_op`` — this yields a genuine
one-op-at-a-time step stream with each op's own ``OperationMeta``. (The documented
fallback — enqueue all, a single ``run_ops``, coarser steps — is unnecessary here since
the per-op path runs reliably on the installed pyinfra.)
"""

from __future__ import annotations

import logging
from typing import Callable

from pyinfra.api import Config, State
from pyinfra.api.connect import connect_all
from pyinfra.api.deploy import add_deploy
from pyinfra.api.inventory import Inventory
from pyinfra.api.operations import run_host_op
from pyinfra.context import ctx_host, ctx_state

from chef import events
from chef.engine.recipe import Recipe
from chef.types import SshTarget, StepState


class RunPhaseError(RuntimeError):
    """A phase operation failed. Raised fail-loud so the worker marks the bake failed
    (and a ``verify`` failure aborts before any snapshot is taken)."""


def run_phase(
    target: SshTarget,
    recipe: Recipe,
    phase: str,
    inputs: dict,
    emit: Callable[[dict], None],
    releases: dict | None = None,
) -> None:
    """Run one recipe ``phase`` against ``target``, emitting step/line events via ``emit``.

    No-ops (returns immediately) when the recipe leaves the phase empty. Raises
    :class:`RunPhaseError` on the first failed operation. ``releases`` (repo → ``{ref, sha}``)
    is exposed to recipes as ``host.data["chef_releases"]``.
    """
    # A composed recipe contributes several phase callables — each base's own @deploy in
    # stack order, then the recipe's own last. A plain recipe yields a chain of one.
    chain = recipe.phase_chain(phase)
    if not chain:
        return
    composed = len(chain) > 1

    inventory = _build_inventory(target, inputs, releases)
    state = State(inventory, Config(), check_for_changes=False)

    # Forward pyinfra's own log lines (connect/op-start/success/retry/error) live, on top
    # of the per-op OperationMeta output we emit below. Scoped: removed in the finally.
    handler = _LogForwarder(emit)
    pyinfra_logger = logging.getLogger("pyinfra")
    previous_level = pyinfra_logger.level
    pyinfra_logger.addHandler(handler)
    if previous_level == logging.NOTSET or previous_level > logging.INFO:
        pyinfra_logger.setLevel(logging.INFO)

    try:
        connect_all(state)
        host = _only_host(state)

        # Enqueue every callable in the chain; each queued op lands in state.get_op_order().
        # Remember which recipe introduced each op (by first appearance, so it holds however
        # pyinfra orders them) to label its steps in a composed bake.
        op_source: dict[str, str] = {}
        seen: set[str] = set()
        for source_name, phase_callable in chain:
            add_deploy(state, phase_callable)
            for op_hash in state.get_op_order():
                if op_hash not in seen:
                    seen.add(op_hash)
                    op_source[op_hash] = source_name

        op_order = state.get_op_order()
        total = len(op_order)

        with ctx_state.use(state):
            state.is_executing = True
            for index, op_hash in enumerate(op_order, start=1):
                with ctx_host.use(host):
                    succeeded = run_host_op(state, host, op_hash)
                label = op_source.get(op_hash) if composed else None
                _emit_op(state, host, op_hash, index, total, succeeded, emit, label)
    finally:
        pyinfra_logger.removeHandler(handler)
        pyinfra_logger.setLevel(previous_level)


def _emit_op(
    state: State,
    host,
    op_hash: str,
    index: int,
    total: int,
    succeeded: bool,
    emit: Callable[[dict], None],
    source: str | None = None,
) -> None:
    """Turn one executed op into a ``step`` event + its output ``line`` events; raise on failure.

    ``source`` (set only for a composed recipe) prefixes the step name with the recipe the
    op came from, e.g. ``nginx › install nginx`` — so a composed bake's log stays legible.
    """
    name = ", ".join(sorted(state.get_op_meta(op_hash).names)) or f"operation {index}"
    if source:
        name = f"{source} › {name}"

    # A skipped op (e.g. filtered off this host) never completes — treat as a no-op.
    op_data = state.ops[host].get(op_hash)
    op_meta = op_data.operation_meta if op_data else None

    if op_meta is None or not op_meta.is_complete():
        emit(events.step_event(name, index, total, StepState.no_change))
        return

    if not op_meta.did_succeed():
        emit(events.step_event(name, index, total, StepState.failed, op_meta.retry_attempts))
        _emit_output(op_meta, emit)
        message = f"operation failed: {name}"
        emit(events.line_event(message))
        raise RunPhaseError(message)

    step_state = StepState.changed if op_meta.did_change() else StepState.no_change
    emit(events.step_event(name, index, total, step_state, op_meta.retry_attempts))
    _emit_output(op_meta, emit)


def _emit_output(op_meta, emit: Callable[[dict], None]) -> None:
    """Emit each captured stdout then stderr line, collapsing embedded ``\\r`` to overwrites."""
    for line in op_meta.stdout_lines:
        _emit_stream_text(line, emit)
    for line in op_meta.stderr_lines:
        _emit_stream_text(line, emit)


def _emit_stream_text(text: str, emit: Callable[[dict], None]) -> None:
    """Split on ``\\r``: every segment but the last is a progress overwrite; the last is a line."""
    if "\r" not in text:
        emit(events.line_event(text))
        return
    segments = text.split("\r")
    for partial in segments[:-1]:
        emit(events.overwrite_event(partial))
    emit(events.line_event(segments[-1]))


class _LogForwarder(logging.Handler):
    """A logging handler that forwards each pyinfra log record as a ``line``/``overwrite`` event."""

    def __init__(self, emit: Callable[[dict], None]):
        super().__init__(level=logging.INFO)
        self._emit = emit

    def emit(self, record: logging.LogRecord) -> None:  # logging.Handler API method
        try:
            _emit_stream_text(record.getMessage(), self._emit)
        except Exception:  # noqa: BLE001 - a broken sink must never crash the bake
            # Canonical logging idiom; must not re-log (would recurse into this handler).
            self.handleError(record)


def _build_inventory(target: SshTarget, inputs: dict, releases: dict | None = None) -> Inventory:
    """One-host pyinfra inventory for ``target``, with ``inputs`` attached as host data.

    ``inputs`` becomes ``host.data`` (recipes call ``host.data.get(...)``); for the ssh
    connector the connection knobs (``ssh_*``) are merged in and win on any key clash.
    Resolved tracked releases are attached under the reserved key ``chef_releases``
    (repo → ``{ref, sha}``), kept distinct from user inputs.
    """
    data = dict(inputs or {})
    data["chef_releases"] = releases or {}
    connector = target.connector

    if connector == "local":
        name = "@local"
    elif connector == "docker":
        # pyinfra's @docker connector runs ops in the named container — no sshd needed.
        name = f"@docker/{target.vm_ref or target.host}"
    else:  # "ssh": a plain hostname routes through the ssh execution connector.
        name = target.host
        ssh_data = {
            "ssh_hostname": target.host,
            "ssh_user": target.user,
            "ssh_port": target.port,
        }
        if target.key_file:
            ssh_data["ssh_key"] = target.key_file
        if target.ssh_config_file:
            ssh_data["ssh_config_file"] = target.ssh_config_file
        data.update(ssh_data)

    return Inventory(([(name, data)], {}))


def _only_host(state: State):
    """The single active host, after ``connect_all``. Fail loud if the target is unreachable."""
    hosts = list(state.inventory.get_active_hosts())
    if not hosts:
        raise RunPhaseError("no reachable host — pyinfra could not connect to the target")
    return hosts[0]
