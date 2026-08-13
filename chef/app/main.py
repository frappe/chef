"""The Chef FastAPI application.

Assembles the routers (``/recipes``, ``/bakes``, ``/images``) behind the bearer-token
dependency, plus the unauthenticated discovery surface (``/healthz``, ``/llms.txt``,
``/openapi.json``, ``/docs``). Errors render as :class:`~chef.schemas.ErrorOut`. On
startup the SQLite schema is created; if a built frontend (``frontend/dist``) is present
it is served as an SPA at ``/``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from http import HTTPStatus
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from chef import __version__, store
from chef.app.auth import require_token
from chef.app.routers import bakes, images, recipes, releases
from chef.config import get_settings
from chef.schemas import ErrorOut

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FRONTEND_DIST = _REPO_ROOT / "frontend" / "dist"

_DESCRIPTION = """\
Chef bakes VM images from declarative **recipes** — like Packer, but built for agents:
strong OpenAPI, machine-readable recipe manifests, and streamed per-step results.

* **recipes** — browse, template, validate and bake.
* **bakes** — track a run, stream its logs (SSE), abort it.
* **images** — list produced artifacts and (M2) install them onto hosts.

Auth: send `Authorization: Bearer <token>` (dev default `chef-dev-token`).
`/healthz`, `/llms.txt`, `/openapi.json` and `/docs` need no token.
See `GET /llms.txt` for an agent-oriented tour.
"""

_TAGS_METADATA = [
    {"name": "recipes", "description": "Browse, validate and bake recipes."},
    {"name": "bakes", "description": "Track, stream and abort bakes."},
    {"name": "images", "description": "Produced images and installation."},
    {"name": "releases", "description": "Per-repo release pins for tracked recipes."},
    {"name": "meta", "description": "Health and machine-readable API tour."},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db()
    yield


def _error_response(status_code: int, detail: object) -> JSONResponse:
    try:
        phrase = HTTPStatus(status_code).phrase
    except ValueError:
        phrase = "error"
    error = phrase.lower().replace(" ", "_")
    body = ErrorOut(error=error, detail=None if detail is None else str(detail))
    return JSONResponse(status_code=status_code, content=body.model_dump())


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Chef",
        version=__version__,
        description=_DESCRIPTION,
        summary="A declarative, agent-friendly image-baking service.",
        openapi_tags=_TAGS_METADATA,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Uniform ErrorOut bodies for HTTP + validation failures.
    @app.exception_handler(StarletteHTTPException)
    async def _on_http_exc(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        resp = _error_response(exc.status_code, exc.detail)
        if exc.headers:
            resp.headers.update(exc.headers)
        return resp

    @app.exception_handler(RequestValidationError)
    async def _on_validation_exc(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _error_response(422, exc.errors())

    # Protected API surface.
    protected = [Depends(require_token)]
    unauthorized = {401: {"model": ErrorOut, "description": "Missing/invalid bearer token."}}
    app.include_router(
        recipes.router, prefix="/recipes", tags=["recipes"],
        dependencies=protected, responses=unauthorized,
    )
    app.include_router(
        bakes.router, prefix="/bakes", tags=["bakes"],
        dependencies=protected, responses=unauthorized,
    )
    app.include_router(
        images.router, prefix="/images", tags=["images"],
        dependencies=protected, responses=unauthorized,
    )
    app.include_router(
        releases.router, prefix="/releases", tags=["releases"],
        dependencies=protected, responses=unauthorized,
    )

    @app.get("/healthz", operation_id="healthz", tags=["meta"], summary="Liveness probe")
    def healthz() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get(
        "/llms.txt",
        operation_id="llms_txt",
        tags=["meta"],
        summary="Agent-oriented plain-text API tour",
        response_class=PlainTextResponse,
    )
    def llms_txt() -> str:
        return _render_llms_txt(app)

    # Serve the built SPA last so API routes win; only if a build exists.
    if _FRONTEND_DIST.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")

    return app


def _render_llms_txt(app: FastAPI) -> str:
    """A concise, plain-text tour of the API, generated from the live route table."""
    lines: list[str] = [
        "# Chef",
        "",
        f"Chef v{__version__} — a declarative, agent-friendly image-baking service.",
        "Recipes (pyinfra deploy + recipe.toml) bake into cold/warm VM images that are",
        "published to an object store and/or installed onto hosts.",
        "",
        "## Auth",
        "Send `Authorization: Bearer <token>` on every /recipes, /bakes and /images call.",
        "Dev default token: `chef-dev-token`.",
        "Public (no token): /healthz, /llms.txt, /openapi.json, /docs.",
        "",
        "## Conventions",
        "- JSON in/out; errors are `{error, detail}` (ErrorOut).",
        "- Full machine-readable schema at GET /openapi.json ; interactive docs at /docs.",
        "- Bake logs stream as Server-Sent Events: event types line/overwrite/step/status/done;",
        "  `done.exit_code == 0` means success.",
        "",
        "## Endpoints",
    ]
    rows: list[tuple[str, str, str]] = []
    for path, operations in app.openapi().get("paths", {}).items():
        for method, op in operations.items():
            if method.upper() in ("HEAD", "OPTIONS", "PARAMETERS"):
                continue
            summary = op.get("summary") or op.get("operationId", "")
            rows.append((method.upper(), path, summary))
    for method, path, summary in sorted(rows, key=lambda r: (r[1], r[0])):
        lines.append(f"- {method:6} {path:30} {summary}")
    lines.extend(
        [
            "",
            "## Typical flow",
            "1. GET /recipes/ — pick a recipe (e.g. `nginx`).",
            "2. GET /recipes/{name} — read its input_schema.",
            "3. POST /recipes/validate — check inputs + phase imports (no bake).",
            "4. POST /recipes/{name}/bake — body {inputs, mode, builder?, idempotency_key?}",
            "   → 202 {bake_id, status, links}.",
            "5. GET /bakes/{id}/logs — stream progress (SSE) until `done`.",
            "6. GET /bakes/{id} — final status, steps and produced image ids.",
            "7. GET /images/{id} — the produced artifact and its location.",
            "",
        ]
    )
    return "\n".join(lines)


app = create_app()
