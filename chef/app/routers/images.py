"""``/images`` — browse produced images and (M2) install one onto a host.

Images are durable :class:`~chef.store.ImageRecord` rows written by the bake pipeline's
publishers. ``install`` is a stub until the Atlas builder lands in M2.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path

from chef.schemas import (
    ErrorOut,
    ImageLocationOut,
    ImageOut,
    InstallRequest,
    InstallResult,
)
from chef.store import ImageRecord, get_image, list_images
from chef.types import SnapshotKind

router = APIRouter()


def _image_out(r: ImageRecord) -> ImageOut:
    return ImageOut(
        id=r.id,
        bake_id=r.bake_id,
        recipe=r.recipe,
        version=r.version,
        kind=SnapshotKind(r.kind),
        base_image=r.base_image,
        location=ImageLocationOut(type=r.location_type, uri=r.location_uri, manifest=r.manifest),
        provenance=r.provenance,
        size_bytes=r.size_bytes,
        host_signature=r.host_signature,
        created_at=r.created_at,
    )


@router.get(
    "/",
    response_model=list[ImageOut],
    operation_id="list_images",
    summary="List produced images",
)
def list_all_images() -> list[ImageOut]:
    return [_image_out(r) for r in list_images()]


@router.get(
    "/{image_id}",
    response_model=ImageOut,
    operation_id="get_image",
    summary="Get one image",
    responses={404: {"model": ErrorOut, "description": "No such image."}},
)
def get_one_image(image_id: str = Path(..., description="The image id.")) -> ImageOut:
    record = get_image(image_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"image '{image_id}' not found")
    return _image_out(record)


@router.post(
    "/{image_id}/install",
    response_model=InstallResult,
    status_code=501,
    operation_id="install_image",
    summary="Install an image onto a host (M2)",
    responses={
        404: {"model": ErrorOut, "description": "No such image."},
        501: {"model": InstallResult, "description": "Not implemented until M2."},
    },
)
def install_image(
    image_id: str = Path(..., description="The image id."),
    body: InstallRequest | None = None,
) -> InstallResult:
    if get_image(image_id) is None:
        raise HTTPException(status_code=404, detail=f"image '{image_id}' not found")
    # M0: real install (place a VM from the base image via a Builder) lands in M2.
    return InstallResult(ok=False, detail="install requires the atlas builder (M2)")
