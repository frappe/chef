"""A tiny httpx client for Atlas's whitelisted service API.

This is the *only* place chef speaks to Atlas, and it speaks over Atlas's public HTTP
surface (never boat, never the DB): the Frappe whitelisted methods under
``{atlas_url}/api/method/atlas.atlas.api.service.<fn>``, authenticated with an
``Authorization: token <key>:<secret>`` header, POSTed with a JSON body, and unwrapped
from Frappe's ``{"message": <value>}`` envelope.

Only :class:`~chef.builders.atlas.AtlasBuilder` and
:class:`~chef.publishers.atlas.AtlasPublisher` construct this — chef core never imports it.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from chef.config import Settings, get_settings

_METHOD_PREFIX = "/api/method/atlas.atlas.api.service."


class AtlasError(RuntimeError):
    """An Atlas API call failed — a transport/HTTP error or a Frappe exception payload.

    ``status`` is the HTTP status (``0`` for a client-side/config failure); ``message`` is
    the best human-readable explanation we could pull out of Frappe's response."""

    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(f"Atlas API error ({status}): {message}")


def _drop_none(params: dict) -> dict:
    return {k: v for k, v in params.items() if v is not None}


def _error_message(payload: Any, fallback: str) -> str:
    """Dig a readable message out of a Frappe error body (``_server_messages`` / ``exception``)."""
    if isinstance(payload, dict):
        server_messages = payload.get("_server_messages")
        if server_messages:
            try:
                parsed = json.loads(server_messages)
                texts = [json.loads(m).get("message", m) if isinstance(m, str) else str(m) for m in parsed]
                if texts:
                    return "; ".join(str(t) for t in texts)
            except (ValueError, TypeError):
                return str(server_messages)
        for key in ("exception", "exc_type", "message", "_error_message", "error"):
            value = payload.get(key)
            if value:
                return str(value)
    return (fallback or "").strip() or "unknown error"


class AtlasClient:
    """Typed convenience wrapper over the Atlas ``service`` endpoints."""

    def __init__(
        self,
        url: str,
        api_key: str,
        api_secret: str,
        timeout: float = 120,
        transport: httpx.BaseTransport | None = None,
    ):
        self.url = url.rstrip("/")
        self._http = httpx.Client(
            base_url=self.url,
            timeout=timeout,
            headers={"Authorization": f"token {api_key}:{api_secret}"},
            transport=transport,
        )

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "AtlasClient":
        """Build a client from :func:`chef.config.get_settings`; fail clearly if unconfigured."""
        settings = settings or get_settings()
        if not settings.atlas_configured:
            raise AtlasError(
                0,
                "Atlas is not configured — set CHEF_ATLAS_URL, CHEF_ATLAS_API_KEY and "
                "CHEF_ATLAS_API_SECRET",
            )
        return cls(
            settings.atlas_url,  # type: ignore[arg-type]
            settings.atlas_api_key,  # type: ignore[arg-type]
            settings.atlas_api_secret,  # type: ignore[arg-type]
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "AtlasClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- the one transport method every typed call funnels through ------------

    def call(self, fn: str, **params: Any) -> Any:
        """POST ``params`` (JSON) to the ``service.<fn>`` method; return the unwrapped ``message``.

        Raises :class:`AtlasError` on a transport failure, a non-2xx status, or a Frappe
        exception payload."""
        try:
            resp = self._http.post(_METHOD_PREFIX + fn, json=params)
        except httpx.HTTPError as exc:
            raise AtlasError(0, f"{fn}: {exc}") from exc

        try:
            payload: Any = resp.json()
        except ValueError:
            payload = None

        if resp.status_code < 200 or resp.status_code >= 300:
            raise AtlasError(resp.status_code, _error_message(payload, resp.text))

        if isinstance(payload, dict) and (
            payload.get("exc") or payload.get("exc_type") or payload.get("exception")
        ):
            raise AtlasError(resp.status_code, _error_message(payload, resp.text))

        if isinstance(payload, dict) and "message" in payload:
            return payload["message"]
        return payload

    # --- typed endpoints (1:1 with atlas.atlas.api.service.*) -----------------

    def create_bare_vm(
        self,
        *,
        title: str,
        base_image: str,
        vcpus: int,
        memory_megabytes: int,
        disk_gigabytes: int,
        ssh_public_key: str | None = None,
        cpu_max_cores: int | None = None,
        server: str | None = None,
    ) -> dict:
        return self.call(
            "create_bare_vm",
            **_drop_none(
                {
                    "title": title,
                    "base_image": base_image,
                    "vcpus": vcpus,
                    "memory_megabytes": memory_megabytes,
                    "disk_gigabytes": disk_gigabytes,
                    "ssh_public_key": ssh_public_key,
                    "cpu_max_cores": cpu_max_cores,
                    "server": server,
                }
            ),
        )

    def stop_vm(self, vm: str) -> Any:
        return self.call("stop_vm", vm=vm)

    def start_vm(self, vm: str) -> Any:
        return self.call("start_vm", vm=vm)

    def terminate_vm(self, vm: str) -> Any:
        return self.call("terminate_vm", vm=vm)

    def snapshot_vm(self, vm: str, title: str | None = None, live: bool = False) -> str:
        return self.call("snapshot_vm", **_drop_none({"vm": vm, "title": title, "live": live}))

    def capture_warm_snapshot(self, vm: str, title: str | None = None) -> str:
        return self.call("capture_warm_snapshot", **_drop_none({"vm": vm, "title": title}))

    def promote_image(self, snapshot: str, image_name: str, title: str | None = None) -> str:
        return self.call(
            "promote_image",
            **_drop_none({"snapshot": snapshot, "image_name": image_name, "title": title}),
        )

    def upload_image_to_s3(self, snapshot: str) -> Any:
        return self.call("upload_image_to_s3", snapshot=snapshot)

    def publish_snapshot_as_fleet_image(
        self,
        *,
        snapshot: str,
        image_name: str,
        servers: list[str] | None = None,
    ) -> dict:
        """Distribute a cold snapshot to the host fleet as a base image (squash+pack to
        S3 + mint a non-local image + fan out sync-image). Returns the Atlas dict."""
        return self.call(
            "publish_snapshot_as_fleet_image",
            **_drop_none(
                {
                    "snapshot": snapshot,
                    "image_name": image_name,
                    "servers": json.dumps(servers) if servers is not None else None,
                }
            ),
        )

    def distribute_image(self, image: str, servers: list[str] | None = None) -> dict:
        """Fan an already-promoted LOCAL base image out to the fleet host-to-host over HTTP
        — no object store. The no-bucket counterpart to
        :meth:`publish_snapshot_as_fleet_image`: Atlas ships the image's base LV straight
        from its home host to every other Active host (or ``servers`` if given), reusing its
        ``sync-image`` verb. Atlas runs the fan-out on its background (``long``) queue and
        returns the ``{image, source, servers}`` handle immediately — call it right after a
        promote to propagate the golden across the fleet without a bucket."""
        return self.call(
            "distribute_image",
            **_drop_none(
                {
                    "image": image,
                    "servers": json.dumps(servers) if servers is not None else None,
                }
            ),
        )

    def get_virtual_machine(self, name: str) -> dict:
        return self.call("get_virtual_machine", name=name)

    def get_snapshot(self, name: str) -> dict:
        return self.call("get_snapshot", name=name)

    def get_image(self, name: str) -> dict:
        return self.call("get_image", name=name)

    def get_server(self, name: str) -> dict:
        return self.call("get_server", name=name)

    def register_bench_snapshot(self, snapshot: str) -> str:
        """Wire a snapshot in as Atlas's ``default_bench_snapshot`` — the golden a self-serve
        Site clones from. The signup counterpart to :meth:`register_user_image`: a Site's VM
        is cloned from a snapshot (not laid down from a base image), so promoting a base image
        is not enough to feed signups. Atlas rejects a non-Available snapshot."""
        return self.call("register_bench_snapshot", snapshot=snapshot)

    def register_user_image(self, image: str) -> str:
        """Wire a base image in as Atlas's ``default_user_image`` — the image a server
        (``create_vm``) boots when no per-version image matches. Call it after ``promote_image``
        (+ ``distribute_image``). Atlas rejects an inactive image."""
        return self.call("register_user_image", image=image)
