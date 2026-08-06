# Chef spec

The source of truth for chef. Read this before changing anything in the service.

Chef is a **general-purpose** system for baking machine images: take a blank VM, run a
declarative **recipe** on it over SSH, snapshot the result (warm **and** cold), and
publish it as an **image** to an object store and/or directly onto hosts — like Packer,
but built for both humans and agents (great OpenAPI, machine-readable recipe manifests,
one-command install).

## The generic core model (five concepts, none workload-specific)

- **Recipe** — a directory: a [pyinfra](https://pyinfra.com) `@deploy` (ordered, retryable
  operations) + a `recipe.toml` manifest (base image, VM size, supported `modes`, typed
  `inputs`, `build`/`verify`/`warm_arm` phases, `[[publish]]` targets). The extension
  point; everything domain-specific is a recipe. Loaded by `chef/engine/recipe.py`.
  Recipes are **algebraic**: a manifest may `compose = ["a", "b"]` to stack other recipes in
  a definite order and add its own steps/overrides — see decision #12.
- **Bake** — one run: a durable arq job (`chef/worker/bake_job.py`) that drives a recipe
  through the pipeline and emits streamed, per-step results. `bake_id` = uuid4.
- **Image** — a produced artifact: cold or warm, with provenance
  (recipe+version+inputs+SHAs), a host signature (warm only), and where its bytes live.
  `image_id` = uuid4. Indexed in SQLite (`chef/store.py`).
- **Builder** *(pluggable, `chef/builders/base.py`)* — supplies a blank VM and performs the
  host-side snapshot: `acquire → snapshot → release` (+ `stop`/`start`/`host_signature`).
  `AtlasBuilder` is the production default; `DockerBuilder`/`LocalBuilder` need no fleet.
- **Publisher** *(pluggable, `chef/publishers/base.py`)* — sends an image to a destination:
  `publish(snapshot) → ImageLocation`. `S3Publisher`, `AtlasPublisher`, `LocalPublisher`.
  A bake may run several (one per `[[publish]]` block).

**Packer mapping:** Builder = builder, recipe = provisioner, Publisher = post-processor.
Atlas/boat are simply the default Builder + Publisher — never baked into chef's vocabulary.

## Locked decisions (abridged; full rationale in the project plan)

1. **Workload-agnostic core.** No Frappe/bench/proxy/site concepts anywhere in core — all
   live inside recipes. bench & proxy are two shipped recipes, not special cases.
2. **Pluggable Builder/Publisher.** Atlas is the default backend; Docker/Local exist for
   authoring/testing without a fleet.
3. **Engine: pyinfra 3.x** embedded; every op inline-retryable; one op at a time for
   structured per-step events.
4. **Phases:** `build` (required), `verify` (optional fail-loud gate before snapshot),
   `warm_arm` (optional, before a warm capture).
5. **`mode=both`** produces cold **and** warm from ONE scratch VM and ONE build: cold while
   stopped, then start → `warm_arm` → warm.
6. **Warm cross-host from day one:** each warm image records its host signature; installs
   only onto signature-compatible hosts.
7. **Object store:** S3-compatible (MinIO dev, DO Spaces / S3 prod).
8. **Recipes live in-repo** under `recipes/`; contributing is a PR.
9. **Chef ↔ boat is never direct** — `AtlasBuilder` drives all snapshot/promote/upload
   verbs through Atlas's API; Atlas SSHes the host to run `boat`.
10. **Chef reaches IPv6-only guests via SSH ProxyJump** through the guest's host — chef's key
    is injected into both host and guest `authorized_keys` via Atlas's `service_public_keys`.
11. **Standalone FastAPI** (not a Frappe app); **arq + Redis** jobs; **Redis Streams** log
    bus; **SSE** streaming; **SQLite** index; **static bearer token** auth (v1); **uv**
    packaging (Python 3.12).
12. **Composable recipes.** A recipe.toml may `compose = [...]` to stack other recipes,
    resolved at load time into a deterministic linearization (bases depth-first, de-duplicated
    first-wins, self last). Manifests merge by a fixed algebra — base_image agree-or-explicit,
    size per-field max, modes intersection (or explicit), inputs union (later wins), publish
    own-only — and each phase runs every stacked recipe's `@deploy` in order. A pure
    composition needs no `recipe.py`. Composition is filesystem + engine only; the bake
    pipeline is unchanged. See `CONTRACT.md`.

## Architecture

`chef-api` (uvicorn/FastAPI) + `chef-worker` (arq) + Redis (+ MinIO for S3). The worker's
bake job: `builder.acquire` → pyinfra provision (`build` → `verify`) → post-process
(cold: stop→snapshot; warm: start→warm_arm→snapshot; both: one VM, cold then warm) →
`publisher.publish` per `[[publish]]` → write Image row(s). Per-step events flow through
Redis Streams to the SSE endpoint and the UI.

## Layout

```
chef/  app/  worker/  engine/  builders/  publishers/  store.py  cli.py  config.py  types.py  events.py
recipes/  hello/ nginx/ bench/ proxy/ chef/
frontend/ (Vue3 + Vite + frappe-ui SPA)
spec/  install.sh  docker-compose.yml  pyproject.toml
```

See `CONTRACT.md` at the repo root for the exact internal signatures each module builds
against.
