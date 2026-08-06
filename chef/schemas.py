"""FastAPI request/response models.

Agent-oriented: enum'd statuses, ISO-8601 datetimes, HATEOAS-lite ``links`` on a bake,
and ``examples`` so the OpenAPI + ``/llms.txt`` read well for both humans and tools.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from chef.types import BakeState, Mode, SnapshotKind, StepState

# --- recipes -----------------------------------------------------------------


class SizeOut(BaseModel):
    vcpus: int
    memory_megabytes: int
    disk_gigabytes: int
    build_memory_megabytes: int = 0


class RecipeSummary(BaseModel):
    name: str
    version: str
    description: str = ""
    base_image: str
    modes: list[str] = ["cold"]
    tags: list[str] = []
    compose: list[str] = Field(
        default_factory=list, description="Base recipes this one stacks, in declared order."
    )


class RecipeDetail(RecipeSummary):
    phases: dict[str, str]
    size: SizeOut
    input_schema: dict = Field(default_factory=dict, description="JSON Schema for `inputs`.")
    publish: list[dict] = []
    source: dict[str, str] = Field(default_factory=dict, description="Recipe source files.")
    lineage: list[str] = Field(
        default_factory=list,
        description="Every recipe run, base-first, ending with this one (self for a plain recipe).",
    )
    phase_sources: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Per phase, the recipes that contribute ops, in run order.",
    )


class ValidationErrorOut(BaseModel):
    message: str
    field: str | None = None
    line: int | None = None


class ValidateRequest(BaseModel):
    name: str
    inputs: dict = Field(default_factory=dict)


class ValidateResult(BaseModel):
    ok: bool
    errors: list[ValidationErrorOut] = []


class TemplateOut(BaseModel):
    files: dict[str, str]


# --- bakes -------------------------------------------------------------------


class Links(BaseModel):
    status: str
    logs: str
    abort: str


class BakeCreate(BaseModel):
    inputs: dict = Field(default_factory=dict)
    mode: Mode = Mode.cold
    builder: str | None = Field(default=None, description="Override the default builder.")
    idempotency_key: str | None = None

    model_config = {
        "json_schema_extra": {
            "examples": [{"inputs": {"worker_processes": "auto"}, "mode": "cold"}]
        }
    }


class BakeAccepted(BaseModel):
    bake_id: str
    status: BakeState
    links: Links


class StepOut(BaseModel):
    index: int
    name: str
    phase: str = ""
    state: StepState
    retries: int = 0


class BakeStatus(BaseModel):
    id: str
    recipe: str
    version: str = ""
    mode: Mode = Mode.cold
    builder: str = "docker"
    status: BakeState
    exit_code: int | None = None
    error: str | None = None
    steps: list[StepOut] = []
    images: list[str] = []
    created_at: datetime
    updated_at: datetime
    links: Links


# --- images ------------------------------------------------------------------


class ImageLocationOut(BaseModel):
    type: str
    uri: str
    manifest: dict = {}


class ImageOut(BaseModel):
    id: str
    bake_id: str
    recipe: str
    version: str = ""
    kind: SnapshotKind
    base_image: str = ""
    location: ImageLocationOut
    provenance: dict = {}
    size_bytes: int = 0
    host_signature: dict | None = None
    created_at: datetime


class InstallRequest(BaseModel):
    server: str | None = Field(default=None, description="Target host; auto-placed if omitted.")
    title: str | None = None


class InstallResult(BaseModel):
    ok: bool
    detail: str = ""
    vm: str | None = None


# --- errors ------------------------------------------------------------------


class ErrorOut(BaseModel):
    error: str
    detail: str | None = None
