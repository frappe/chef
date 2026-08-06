"""``/recipes`` — browse recipes, fetch the authoring template, validate inputs+imports
without baking, and kick off a bake.

Recipes are files on disk (``settings.recipes_dir``); this router only reads them via
``chef.engine.recipe``. A bake creates a durable :class:`~chef.store.BakeRecord` and
enqueues the arq ``bake`` job (id == bake id, so it can later be aborted). If Redis is
unreachable the row is still created and the response reports ``queued`` — the enqueue is
best-effort so the API degrades gracefully.
"""

from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Path

from chef.app.routers.bakes import bake_links
from chef.config import Settings, get_settings
from chef.engine.recipe import (
    Manifest,
    Recipe,
    RecipeError,
    list_manifests,
    load_recipe,
    recipe_template,
)
from chef.schemas import (
    BakeAccepted,
    BakeCreate,
    ErrorOut,
    RecipeDetail,
    RecipeSummary,
    SizeOut,
    TemplateOut,
    ValidateRequest,
    ValidateResult,
    ValidationErrorOut,
)
from chef.store import BakeRecord, create_bake, find_bake_by_idempotency
from chef.types import BakeState

logger = logging.getLogger("chef.recipes")

router = APIRouter()


# --- mappers -----------------------------------------------------------------


def _summary(m: Manifest) -> RecipeSummary:
    return RecipeSummary(
        name=m.name,
        version=m.version,
        description=m.description,
        base_image=m.base_image,
        modes=list(m.modes),
        tags=list(m.tags),
    )


def _size_out(m: Manifest) -> SizeOut:
    return SizeOut(
        vcpus=m.size.vcpus,
        memory_megabytes=m.size.memory_megabytes,
        disk_gigabytes=m.size.disk_gigabytes,
        build_memory_megabytes=m.size.build_memory_megabytes,
    )


def _detail(recipe: Recipe) -> RecipeDetail:
    m = recipe.manifest
    return RecipeDetail(
        **_summary(m).model_dump(),
        phases={k: v for k, v in m.phases.items() if v},
        size=_size_out(m),
        input_schema=recipe.input_schema(),
        publish=list(m.publish),
        source=recipe.source(),
    )


def _verr(exc: RecipeError) -> ValidationErrorOut:
    return ValidationErrorOut(**exc.as_dict())


def _accepted(record: BakeRecord) -> BakeAccepted:
    return BakeAccepted(
        bake_id=record.id,
        status=BakeState(record.status),
        links=bake_links(record.id),
    )


# --- endpoints ---------------------------------------------------------------


@router.get(
    "/",
    response_model=list[RecipeSummary],
    operation_id="list_recipes",
    summary="List available recipes",
)
def list_recipes(settings: Settings = Depends(get_settings)) -> list[RecipeSummary]:
    return [_summary(m) for m in list_manifests(settings.recipes_dir)]


@router.get(
    "/template",
    response_model=TemplateOut,
    operation_id="recipe_template",
    summary="Get the recipe authoring skeleton",
)
def get_recipe_template() -> TemplateOut:
    return TemplateOut(files=recipe_template())


@router.post(
    "/validate",
    response_model=ValidateResult,
    operation_id="validate_recipe",
    summary="Validate a recipe's inputs + phase imports (no bake)",
)
def validate_recipe(
    body: ValidateRequest, settings: Settings = Depends(get_settings)
) -> ValidateResult:
    errors: list[ValidationErrorOut] = []
    try:
        recipe = load_recipe(settings.recipes_dir, body.name)
    except RecipeError as exc:
        return ValidateResult(ok=False, errors=[_verr(exc)])

    try:
        recipe.validate_inputs(body.inputs)
    except RecipeError as exc:
        errors.append(_verr(exc))

    # Import each configured phase to surface import errors early (the agent fix loop).
    for phase in ("build", "verify", "warm_arm"):
        if recipe.has_phase(phase):
            try:
                recipe.load_phase(phase)
            except RecipeError as exc:
                errors.append(_verr(exc))

    return ValidateResult(ok=len(errors) == 0, errors=errors)


@router.get(
    "/{name}",
    response_model=RecipeDetail,
    operation_id="get_recipe",
    summary="Get a recipe's manifest, input schema and source",
    responses={404: {"model": ErrorOut, "description": "No such recipe."}},
)
def get_recipe(name: str = Path(..., description="Recipe directory name.")) -> RecipeDetail:
    settings = get_settings()
    try:
        recipe = load_recipe(settings.recipes_dir, name)
    except RecipeError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    return _detail(recipe)


async def _enqueue_bake(bake_id: str, settings: Settings) -> bool:
    """Enqueue the arq ``bake`` job with ``_job_id == bake_id``. False if redis is down."""
    from arq import create_pool
    from arq.connections import RedisSettings

    try:
        pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    except Exception:  # noqa: BLE001 - redis down: keep the row, report queued
        logger.warning("enqueue: cannot reach redis for bake %s", bake_id, exc_info=True)
        return False
    try:
        await pool.enqueue_job("bake", bake_id, _job_id=bake_id)
        return True
    except Exception:  # noqa: BLE001
        logger.warning("enqueue: failed for bake %s", bake_id, exc_info=True)
        return False
    finally:
        try:
            await pool.aclose()
        except Exception:  # noqa: BLE001
            pass


@router.post(
    "/{name}/bake",
    status_code=202,
    response_model=BakeAccepted,
    operation_id="create_bake",
    summary="Validate inputs, create a bake and enqueue it",
    responses={
        202: {"model": BakeAccepted, "description": "Bake accepted (queued)."},
        404: {"model": ErrorOut, "description": "No such recipe."},
        422: {"model": ErrorOut, "description": "Inputs failed the recipe's schema."},
    },
)
async def create_bake_for_recipe(
    body: BakeCreate,
    name: str = Path(..., description="Recipe directory name."),
    settings: Settings = Depends(get_settings),
) -> BakeAccepted:
    try:
        recipe = load_recipe(settings.recipes_dir, name)
    except RecipeError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc

    try:
        resolved = recipe.validate_inputs(body.inputs)
    except RecipeError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc

    # Idempotency: replay the same key → return the existing bake unchanged.
    if body.idempotency_key:
        existing = find_bake_by_idempotency(body.idempotency_key)
        if existing is not None:
            return _accepted(existing)

    bake_id = str(uuid4())
    record = BakeRecord(
        id=bake_id,
        recipe=name,
        version=recipe.manifest.version,
        mode=body.mode.value,
        builder=body.builder or settings.default_builder,
        inputs=resolved,
        status="queued",
        idempotency_key=body.idempotency_key,
    )
    create_bake(record)
    await _enqueue_bake(bake_id, settings)  # best-effort; row stands even if redis is down
    return _accepted(record)
