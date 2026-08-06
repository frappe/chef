#!/bin/sh
# Chef one-command installer:  curl -LsSf https://raw.githubusercontent.com/frappe/chef/main/install.sh | sh
#
# Ensures uv, installs the `chef` CLI, then runs `chef install-service` — which bakes the
# `chef` recipe against pyinfra's @local connector (uv + redis + the chef-api & chef-worker
# systemd units). Idempotent and re-runnable. Extra args pass through to install-service,
# e.g.  ... | sh -s -- --redis-url redis://localhost:6379
set -eu

CHEF_SOURCE="${CHEF_SOURCE:-git+https://github.com/frappe/chef}"

# 1. uv — pinned installer, skipped if already present.
if ! command -v uv >/dev/null 2>&1; then
	curl -LsSf https://astral.sh/uv/install.sh | sh
fi
# uv drops its shims under ~/.local/bin; put chef/uv on PATH for the rest of this script.
export PATH="$HOME/.local/bin:$PATH"

# 2. the chef CLI.
uv tool install --force "$CHEF_SOURCE"

# 3. install chef as a service on this host (the `chef` recipe against @local).
exec chef install-service "$@"
