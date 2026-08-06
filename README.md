# Chef

A generic, declarative image-baking platform.

Chef takes a blank VM, runs a declarative **recipe** on it over SSH, snapshots the
result (warm **and** cold), and publishes it as an **image** to an object store and/or
directly onto hosts — like Packer, but built for both humans and agents: great OpenAPI,
machine-readable recipe manifests, and one-command install.

Chef's core knows nothing about any particular workload. It does not know what a bench,
a proxy, a database, or a "site" is. All of that lives **inside recipes**. Where the VM
comes from (**Builder**) and where the image goes (**Publisher**) are pluggable backends.

## Concepts

- **Recipe** — a directory: a [pyinfra](https://pyinfra.com) deploy + a `recipe.toml`
  manifest (base image, VM size, supported modes, typed inputs, build/verify/warm_arm
  phases). The extension point; everything domain-specific is a recipe.
- **Bake** — one run: a durable job that drives a recipe through the pipeline and emits
  streamed, per-step results.
- **Image** — a produced artifact: cold or warm, with provenance and a location.
- **Builder** *(pluggable)* — supplies a blank VM and performs host-side snapshots
  (`Docker`, `Local`, `Atlas`).
- **Publisher** *(pluggable)* — sends an image to a destination (`Local`, `S3`, `Atlas`).

The Packer mapping: **Builder = builder, recipe = provisioner, Publisher = post-processor.**

## Quick start (dev, no fleet)

```sh
docker compose up            # chef-api, chef-worker, redis, minio
# open http://localhost:8000/docs  and  http://localhost:5173 (UI)
```

See `spec/` for the source-of-truth design.

## Status

Under active construction. See the milestone plan in the project tracker.
