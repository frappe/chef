# Contributing to chef

Chef is a general-purpose, declarative image-baking platform: take a blank VM, run a
**recipe** on it over SSH, snapshot the result (cold **and** warm), and publish it as an
**image**. The core knows nothing about any particular workload — everything
domain-specific lives inside a recipe. Most contributions are **new recipes**; the rest
are new **Builder** / **Publisher** backends.

## Repo layout

```
chef/        the service — app/ (FastAPI) · worker/ (arq) · engine/ (recipe + pyinfra runner)
             builders/ · publishers/ · store.py · cli.py · config.py · types.py · events.py
recipes/     the extension point — hello/ nginx/ bench/ proxy/ chef/ (chef installs itself)
frontend/    Vue 3 + Vite SPA
spec/        design source of truth — read spec/README.md first
CONTRACT.md  the fixed internal signatures every module builds against
```

- **`spec/README.md`** is the source of truth for what chef is and the locked decisions.
  Read it before changing anything in the service.
- **`CONTRACT.md`** pins the spine signatures (`types.py`, `events.py`, `config.py`,
  `schemas.py`, `store.py`, `builders/base.py`, `publishers/base.py`, `engine/recipe.py`).
  Do not change those without flagging it — other modules depend on their exact shapes.

## Authoring a recipe

A recipe is a directory under `recipes/<name>/`:

- **`recipe.toml`** — the manifest: `name`, `version`, `base_image`, `modes`
  (`cold`/`warm`), typed `[inputs.*]` (JSON-Schema-shaped, validated + defaulted on bake),
  `[phases]` (`build` required; `verify` optional fail-loud gate; `warm_arm` optional,
  before a warm capture), `[size]`, and `[[publish]]` blocks.
- **`recipe.py`** — pure [pyinfra](https://pyinfra.com) `@deploy` callables, one per phase.
  **No chef/atlas imports** — recipes read their inputs from `host.data.get("...")` and use
  ordered, retryable pyinfra operations. See `recipes/nginx/recipe.py` for the canonical
  style (`apt.packages` → `files.template` → `server.service` + a `verify` curl).
- **`templates/`** *(optional)* — Jinja2 templates rendered with `files.template`; resolve
  their paths absolutely (`os.path.join(os.path.dirname(__file__), "templates", ...)`).

Scaffold the skeleton, then edit:

```sh
.venv/bin/chef new myrecipe        # writes recipe.toml + recipe.py from the template
```

Validate without baking (the write → fix loop) — either the API or a local load:

```sh
# API (dev stack up): load + schema + import, no VM touched
curl -s -X POST localhost:8000/recipes/validate -H 'content-type: application/json' \
     -d '{"name":"myrecipe"}'

# or exercise it locally against pyinfra's @local connector (no fleet, no Docker)
.venv/bin/chef bake myrecipe --builder local -i somekey=somevalue
```

Recipes live in-repo; contributing one is a PR (spec decision #8).

## Dev stack

```sh
docker compose up            # chef-api + chef-worker + redis + minio
# http://localhost:8000/docs (API)   http://localhost:9001 (MinIO console)
```

## Tests

```sh
.venv/bin/pytest             # whole suite
.venv/bin/pytest tests/test_recipe.py -q
```

Recipe tests load the manifest, assert phases resolve to callables, and check templates /
inputs — they must **not** actually bake or run `install-service` (that would mutate the
test machine). The `@local` runner path (`tests/test_runner.py`) is safe: it only runs an
`echo` op.

## Extension points

- **Builder** (`chef/builders/base.py`) — supplies a blank VM and takes the host-side
  snapshot: `acquire → snapshot → release` (+ `stop`/`start`/`host_signature`). Register in
  `chef/builders/__init__.py` (`get_builder`). Shipped: `docker`, `local`, `atlas`.
- **Publisher** (`chef/publishers/base.py`) — sends an image to a destination:
  `publish(snapshot) → ImageLocation`. Register in `chef/publishers/__init__.py`
  (`get_publisher`). Shipped: `local`, `s3`, `atlas-base-image`.

Chef core never imports Atlas or the Docker SDK directly — only through a Builder/Publisher.
