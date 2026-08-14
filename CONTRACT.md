# Chef internal contract (build against this)

This file is the fixed contract every module builds against. **Do not change the spine
files** (`chef/types.py`, `chef/events.py`, `chef/config.py`, `chef/schemas.py`,
`chef/store.py`, `chef/builders/base.py`, `chef/publishers/base.py`, `chef/engine/recipe.py`)
without flagging it — other modules depend on their exact signatures.

## Module map & dependency direction

```
config.py   types.py   events.py          # leaves, no chef deps
   │           │           │
   ├───────────┼───────────┤
schemas.py  store.py                       # depend on types/config
engine/recipe.py                           # types
engine/pyinfra_runner.py    → recipe, events, types
builders/{base,docker,local,atlas}.py      → types (base); config for atlas
publishers/{base,local,s3,atlas}.py        → types (base); config for s3/atlas
worker/{settings,bake_job}.py              → engine, builders, publishers, store, events, config
app/{main,auth,sse,routers/*}.py           → store, engine, schemas, worker(enqueue), config
cli.py                                     → uvicorn, arq, engine (bake), config
```

Chef core **never imports Atlas or Docker SDK directly** — only through a Builder/Publisher.

## The bake pipeline (worker/bake_job.py)

`bake(ctx, bake_id)` is the arq task. It reads the `BakeRecord`, resolves the recipe +
builder + publishers, and runs:

```
set_bake(status=acquiring);   emit status
target = builder.acquire(recipe.manifest.base_image, recipe.manifest.size, title=…)
set_bake(status=building);    emit status
run_phase(target, recipe, "build",  inputs, emit)     # engine/pyinfra_runner
if recipe.has_phase("verify"):
    set_bake(status=verifying); run_phase(target, recipe, "verify", inputs, emit)  # fail-loud
set_bake(status=snapshotting)
snapshots = {}
for kind in Mode(mode).kinds():            # cold before warm (decision #7)
    if kind == cold: builder.stop(target);  snapshots[cold] = builder.snapshot(target,"cold",…)
    if kind == warm: builder.start(target)
                     if recipe.has_phase("warm_arm"): run_phase(target,recipe,"warm_arm",inputs,emit)
                     snapshots[warm] = builder.snapshot(target,"warm",…)
set_bake(status=publishing)
for kind, snap in snapshots.items():
    for pub_cfg in recipe.manifest.publish:
        loc = publisher_for(pub_cfg["type"]).publish(snap, recipe=…, version=…, config=pub_cfg)
        create_image(ImageRecord(... location_type=loc.type, location_uri=loc.uri ...))
builder.release(target)
set_bake(status=succeeded, exit_code=0);  emit done(0)
# any exception → emit line(str(exc)); set_bake(failed, error=…); emit done(1); builder.release best-effort
```

`emit(event: dict)` publishes onto Redis Stream key `chef:bake:{bake_id}:log` via
`XADD`. Use `events.py` builders. Also mirror structured `step` events into
`store.record_step(...)`. On `Job.abort()` (arq), catch `asyncio.CancelledError`, set
`aborted`, release the builder, re-raise.

## Release tracking (chef/releases.py + chef/store.py)

- **Store** (`chef/store.py`): `TrackedRelease(repo pk, ref, sha, resolved_at, updated_at)`
  with `get_pin(repo)`, `set_pin(repo, ref, sha)`, `list_pins()`, `delete_pin(repo)`.
  `BakeRecord.releases: dict` = per-bake `{repo: ref}` overrides (one bake, store
  untouched).
- **Resolver** (`chef/releases.py`): `resolve_ref(repo, ref) -> sha|None`,
  `list_refs(repo) -> [tags]` (newest-first), `ReleaseError`; backed by `git ls-remote`
  (no GitHub API/token).
- **Manifest**: `[[track]] repo = "…"` is identity only -> `Manifest.track: list[dict]`,
  unioned in `_merge` (dedup by repo); `Recipe.tracked_repos() -> [repo]`.
- **Pipeline** (`worker/bake_job.py`): before acquire, resolve every tracked repo's
  effective ref (per-bake override else store pin); fail **closed** if any is unpinned;
  resolve ref -> sha; inject `host.data["chef_releases"] = {repo: {ref, sha}}` via
  `run_phase(..., releases=...)`; record `provenance["releases"] = {repo: {ref, sha}}` on
  each image.
- **API** (`app/routers/releases.py`, mounted at `/releases`): `GET /releases/` (list),
  `PUT /releases/ {repo, ref}` (validate + resolve; 422 if ref not found),
  `DELETE /releases/{repo:path}`, `GET /releases/refs?repo=` (tag picker). Plus
  `RecipeDetail.tracked`, `BakeCreate.releases`, `ImageOut.provenance.releases`.

## Streaming (events.py + app/sse.py)

Wire shapes (JSON): `line{line}`, `overwrite{line}`, `step{name,index,total,state,retries}`,
`status{status,phase}`, `done{exit_code,status}`. `done.exit_code == 0` = success.
`app/sse.py` exposes `GET /bakes/{id}/logs` as an `sse-starlette` `EventSourceResponse`
that: (1) reads the whole Redis Stream from `0` (replay), (2) tails via blocking `XREAD`,
(3) stops after `done`. Each SSE message: `event:` = the dict's `type`, `data:` = compact
JSON of the dict (matches the lifted `useTaskStream.js`).

## engine/pyinfra_runner.py

```python
def run_phase(target: SshTarget, recipe: Recipe, phase: str, inputs: dict,
              emit: Callable[[dict], None], releases: dict | None = None) -> None: ...
```
- Build a pyinfra inventory from `target` (connector `ssh` with `ssh_config_file`/`key_file`,
  or `docker`/`local`), setting **host data = `inputs` plus `chef_releases`** (repo ->
  `{ref, sha}`; `releases or {}` when absent) so recipes read `host.data.get(...)`.
- Import the phase callable via `recipe.load_phase(phase)`; it queues ops.
- **Primary path:** run **one op at a time** — for each queued op, `add_op` → `run_ops` →
  read `OperationMeta` (name, `did_change`, retries, output) → `emit(step_event(...))` and
  `emit(line_event(...))` for output. A logging handler streams raw stdout as `line`/
  `overwrite` (collapse `\r`). **Fallback:** queue all ops, one `run_ops`, coarser steps.
- Fail loud: any op failure raises; verify phase failure aborts the bake before snapshot.
- Pin the exact pyinfra 3.x API you use; keep SSH mockable for unit tests.

## Builders (chef/builders/)

Implement `Builder` (base.py). `chef/builders/__init__.py` should expose
`get_builder(name: str) -> Builder`.
- **DockerBuilder** (`name="docker"`): `acquire` starts a container from `base_image`
  (default to an ssh-enabled ubuntu image; a container with sshd, or use pyinfra's
  `@docker` connector so no sshd is needed — prefer `@docker`, set
  `SshTarget(connector="docker", vm_ref=container_id, host=container_id)`). `snapshot` =
  `docker commit` → export to a tar path under a data dir; return `SnapshotRef(ref=path)`.
  `stop`/`start` = docker stop/start. `release` = docker rm -f. No host signature.
- **LocalBuilder** (`name="local"`): `@local` connector; `acquire` returns
  `SshTarget(connector="local", host="@local")`; snapshot is a stub/no-op ref. For authoring.
- **AtlasBuilder** (`name="atlas"`, M2): calls Atlas API (see below). `acquire` →
  `service.create_bare_vm`; writes a per-bake ssh config with `ProxyJump root@<server_ipv4>`
  and returns `SshTarget(connector="ssh", host=guest_ipv6, ssh_config_file=…, vm_ref=vm_name)`;
  poll readiness with system `ssh -J`. `snapshot` → `service.snapshot_vm` /
  `capture_warm_snapshot`. `host_signature` ← `service.get_server`. `release` → terminate.

## Publishers (chef/publishers/)

Implement `Publisher` (base.py). `chef/publishers/__init__.py`: `get_publisher(type) -> Publisher`.
- **LocalPublisher** (`type="local"`): copy/reference bytes under a local images dir; return
  `ImageLocation(type="local", uri="file://…")`.
- **S3Publisher** (`type="s3"`, M1): upload to the configured S3/MinIO bucket; return
  `ImageLocation(type="s3", uri="s3://bucket/key", manifest={...})`.
- **AtlasPublisher** (`type="atlas-base-image"`, M2): `service.promote_image` (+ optional
  `distribute = true` → `service.distribute_image`, + optional `register_user_image = true` →
  `service.register_user_image`, wiring it as Atlas `default_user_image` for servers). `kinds =
  (cold,)` — a warm memory snapshot can't be promoted. `uri` = the base-image name from `config["name"]`.
- **AtlasBenchGoldenPublisher** (`type="atlas-bench-snapshot"`): `service.register_bench_snapshot`
  — wire the cold snapshot as Atlas `default_bench_snapshot`, the golden a self-serve Site clones.
  `kinds = (cold,)`; a `both` bake's warm snapshot is auto-discovered per-host by Atlas, not registered.

## Atlas API (M2; called ONLY by AtlasBuilder/AtlasPublisher via a small httpx client)

Frappe whitelisted methods at `{atlas_url}/api/method/atlas.atlas.api.service.<fn>`, auth
header `Authorization: token <key>:<secret>`, response unwrapped from `{"message": …}`:
- `create_bare_vm(title, base_image, vcpus, memory_megabytes, disk_gigabytes, cpu_max_cores?, server?)`
  → `{name,status,ipv6_address,server,server_ipv4}`
- `snapshot_vm(vm, title?, live?)` → snapshot name; `capture_warm_snapshot(vm, title?)` → name
- `promote_image(snapshot, image_name, title?)` → image name; `distribute_image(image, servers?)` → fan-out handle
- `register_bench_snapshot(snapshot)` → `default_bench_snapshot` (signups); `register_user_image(image)` → `default_user_image` (servers)
- `upload_image_to_s3(snapshot)`; `get_server(name)` → `{…, architecture, kernel_version, firecracker_version, jailer_version}`
- Poll VM/snapshot status via `get_virtual_machine` / a snapshot getter until Available.

## App (chef/app/) — endpoints (models in schemas.py)

```
GET  /recipes/                 -> list[RecipeSummary]
GET  /recipes/template         -> TemplateOut
GET  /recipes/{name}           -> RecipeDetail
POST /recipes/validate         -> ValidateResult            (load+schema+import, no bake)
POST /recipes/{name}/bake      -> 202 BakeAccepted          (validate inputs, create BakeRecord, enqueue arq)
GET  /bakes/{id}               -> BakeStatus
GET  /bakes/{id}/logs          -> SSE (app/sse.py)
POST /bakes/{id}/abort         -> 200                       (arq Job.abort)
GET  /images/                  -> list[ImageOut]
GET  /images/{id}              -> ImageOut
POST /images/{id}/install      -> InstallResult             (M2+, via a Builder)
GET  /openapi.json /llms.txt /healthz
```
Auth: `app/auth.py` a bearer dependency comparing `Authorization: Bearer <token>` to
`settings.api_token` (skip on `/healthz`, `/llms.txt`, `/openapi.json`, `/docs`). CORS from
`settings.cors_origins`. Every route: explicit `operation_id`, `responses` with `ErrorOut`
for 4xx, tags. `main.py` calls `store.init_db()` on startup and serves the built frontend
(`frontend/dist`) if present. `/llms.txt` = a plain-text tour of the API for agents.

## CLI (chef/cli.py) — Typer app; entry `chef.cli:main`
`serve` (uvicorn), `worker` (arq), `bake <recipe> [--input k=v] [--mode]` (enqueue or run
inline), `new <name>` (scaffold from `recipe_template()`), `install-service` (M5). `main()`
invokes the Typer app.

## Recipes (recipes/<name>/): recipe.py (pyinfra @deploy) + recipe.toml. See engine/recipe.py
templates. `hello` = trivial; `nginx` = apt install + enable + a `verify` curl, one string
input `worker_processes`.

## Recipe composition (`compose` — engine/recipe.py, spine change)

A recipe.toml may declare `compose = ["base_a", "base_b"]` (ordered) to **stack** other
recipes. `load_recipe` resolves it: linearize the bases depth-first, de-dup by name (first
wins, so diamonds collapse), append the recipe itself → an ordered **stack** ending in self;
cycles and missing bases raise `RecipeError`. When `compose` is set, `base_image` and
`[phases].build` are optional (inherited). The **merge algebra** (`_merge`): `base_image` =
own-explicit-or-bases-agree; `size` = per-field max (an undeclared derived size doesn't
count); `modes` = own-declared-or-intersection-of-bases; `tags` = union +`composed`; `inputs`
= union, later-in-stack wins (override a default / share an input); `track` = union, dedup by
repo (like inputs); `publish` = own only.
Each phase runs **every stack member's own `@deploy` in order** — `Recipe.phase_chain(phase)`
returns `[(name, callable)]`; `run_phase` `add_deploy`s each; `has_phase` is stack-aware.
Because pyinfra ops resolve assets relative to each `recipe.py`'s `__file__`, a base's
templates/files keep working un-copied. New `Manifest` fields: `compose`, `modes_declared`,
`size_declared`. API: `RecipeSummary.compose`, `RecipeDetail.{lineage, phase_sources}`.
`bake_job.py` is unchanged — resolution lives entirely in `load_recipe`/`Recipe`. Shipped
examples: `webapp` (compose hello+nginx) and `erpnext` (compose pilot). Scaffold a composed
recipe with `chef new <name> --compose a,b`.
```
