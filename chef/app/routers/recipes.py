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
from chef.releases import ReleaseError, resolve_ref
from chef.schemas import (
    BakeAccepted,
    BakeCreate,
    ErrorOut,
    RecipeDetail,
    RecipeSummary,
    SizeOut,
    TemplateOut,
    TrackedReleaseOut,
    ValidateRequest,
    ValidateResult,
    ValidationErrorOut,
)
from chef.store import BakeRecord, create_bake, find_bake_by_idempotency, get_pin
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
        compose=list(m.compose),
    )


def _size_out(m: Manifest) -> SizeOut:
    return SizeOut(
        vcpus=m.size.vcpus,
        memory_megabytes=m.size.memory_megabytes,
        disk_gigabytes=m.size.disk_gigabytes,
        build_memory_megabytes=m.size.build_memory_megabytes,
    )


def _tracked(recipe: Recipe) -> list[TrackedReleaseOut]:
    """Join the recipe's tracked repos with their current store pin (nulls if unpinned)."""
    out: list[TrackedReleaseOut] = []
    for repo in recipe.tracked_repos():
        pin = get_pin(repo)
        out.append(TrackedReleaseOut(
            repo=repo,
            ref=(pin.ref or None) if pin else None,
            sha=(pin.sha or None) if pin else None,
            resolved_at=pin.resolved_at if pin else None,
        ))
    return out


def _detail(recipe: Recipe) -> RecipeDetail:
    m = recipe.manifest
    return RecipeDetail(
        **_summary(m).model_dump(),
        phases={k: v for k, v in m.phases.items() if v},
        size=_size_out(m),
        input_schema=recipe.input_schema(),
        publish=list(m.publish),
        tracked=_tracked(recipe),
        source=recipe.source(),
        lineage=recipe.lineage,
        phase_sources=recipe.phase_sources(),
    )


def _verr(exc: RecipeError) -> ValidationErrorOut:
    return ValidationErrorOut(**exc.as_dict())


def _release_errors(
    recipe: Recipe, overrides: dict[str, str], *, resolve: bool = True
) -> list[ValidationErrorOut]:
    """Validate every tracked repo's effective release the way a bake would resolve it.

    The effective ref is the per-bake override if given, else the store pin. Reports an
    unpinned tracked repo (the fail-closed condition) and, when ``resolve`` is set, a ref
    that does not exist in the repo (``git ls-remote``). ``resolve=False`` skips the network
    check — used at bake creation, where the worker re-resolves authoritatively.
    """
    errors: list[ValidationErrorOut] = []
    for repo in recipe.tracked_repos():
        ref = (overrides or {}).get(repo)
        if not ref:
            pin = get_pin(repo)
            ref = pin.ref if pin else None
        field = f"releases.{repo}"
        if not ref:
            errors.append(ValidationErrorOut(
                message=f"no release pinned for '{repo}' — pin it on the Releases page "
                        f"or override it for this bake",
                field=field,
            ))
            continue
        if not resolve:
            continue
        try:
            sha = resolve_ref(repo, ref)
        except ReleaseError as exc:
            errors.append(ValidationErrorOut(
                message=f"could not verify release for '{repo}': {exc}", field=field))
            continue
        if not sha:
            errors.append(ValidationErrorOut(
                message=f"release ref '{ref}' not found in '{repo}'", field=field))
    return errors


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

    # Import every configured phase across the whole stack (each base's + this recipe's own)
    # to surface import errors early — the agent fix loop, now composition-aware.
    for phase in ("build", "verify", "warm_arm"):
        try:
            recipe.phase_chain(phase)
        except RecipeError as exc:
            errors.append(_verr(exc))

    # Also check the release(s): a tracked repo must be pinned (or overridden) and the
    # effective ref must exist — the same fail-closed condition the bake would hit.
    errors.extend(_release_errors(recipe, body.releases))

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
    """Enqueue the arq ``bake`` job (id == bake id, so it can be aborted). False if redis
    is down — the durable row still stands. Delegates to the shared enqueue path so a UI
    bake and a CLI ``--async`` bake run on the same worker."""
    from chef.worker.settings import enqueue_bake

    ok = await enqueue_bake(bake_id, settings.redis_url)
    if not ok:
        logger.warning("enqueue: could not reach redis for bake %s", bake_id)
    return ok


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

    # Reject a bake whose tracked repo has no pin (or override) up front, rather than
    # enqueueing one the worker will fail closed. Cheap check only — no network resolution
    # here; the worker re-resolves the ref authoritatively at bake time.
    rel_errors = _release_errors(recipe, body.releases, resolve=False)
    if rel_errors:
        raise HTTPException(status_code=422, detail=rel_errors[0].message)

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
        releases=body.releases,
        status="queued",
        idempotency_key=body.idempotency_key,
    )
    create_bake(record)
    await _enqueue_bake(bake_id, settings)  # best-effort; row stands even if redis is down
    return _accepted(record)
