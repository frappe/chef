"""``/releases`` — manage per-repo release pins.

The release-tracking store holds one pin per upstream repo (``frappe/pilot`` → a git ref +
the commit it resolved to). Recipes declare which repos they track (``[[track]]``); this
router is where the pin is set, listed, cleared, and where the tag picker gets its options.
Resolution is pure ``git ls-remote`` (:mod:`chef.releases`) — no GitHub API, no token.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from chef import store
from chef.releases import ReleaseError, list_refs, resolve_ref
from chef.schemas import ErrorOut, RefsOut, SetPinRequest, TrackedReleaseOut

router = APIRouter()


def _out(pin: store.TrackedRelease) -> TrackedReleaseOut:
    return TrackedReleaseOut(
        repo=pin.repo, ref=pin.ref or None, sha=pin.sha or None, resolved_at=pin.resolved_at
    )


@router.get(
    "/",
    response_model=list[TrackedReleaseOut],
    operation_id="list_release_pins",
    summary="List every repo's release pin",
)
def list_pins() -> list[TrackedReleaseOut]:
    return [_out(p) for p in store.list_pins()]


@router.get(
    "/refs",
    response_model=RefsOut,
    operation_id="list_release_refs",
    summary="List a repo's tags (newest first) for the picker",
    responses={502: {"model": ErrorOut, "description": "git ls-remote failed."}},
)
def list_release_refs(repo: str = Query(..., description='Repo, e.g. "frappe/pilot".')) -> RefsOut:
    try:
        return RefsOut(repo=repo, refs=list_refs(repo))
    except ReleaseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.put(
    "/",
    response_model=TrackedReleaseOut,
    operation_id="set_release_pin",
    summary="Pin a repo to a ref (validated + resolved to a SHA)",
    responses={
        422: {"model": ErrorOut, "description": "Ref not found in the repo."},
        502: {"model": ErrorOut, "description": "git ls-remote failed."},
    },
)
def set_release_pin(body: SetPinRequest) -> TrackedReleaseOut:
    try:
        sha = resolve_ref(body.repo, body.ref)
    except ReleaseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not sha:
        raise HTTPException(
            status_code=422, detail=f"ref '{body.ref}' not found in '{body.repo}'"
        )
    return _out(store.set_pin(body.repo, body.ref, sha))


@router.delete(
    "/{repo:path}",
    status_code=204,
    operation_id="delete_release_pin",
    summary="Remove a repo's release pin",
    responses={404: {"model": ErrorOut, "description": "No pin for that repo."}},
)
def delete_release_pin(repo: str) -> None:
    if not store.delete_pin(repo):
        raise HTTPException(status_code=404, detail=f"no release pin for '{repo}'")
