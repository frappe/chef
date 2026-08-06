# recipe.py — pure pyinfra, no chef/atlas imports.
#
# SHIM recipe (plan decision #20): rather than reimplement the golden bench stack
# in native pyinfra ops, this uploads Atlas's committed `bench/` source tree
# verbatim and runs its existing `build.sh` / `warm.sh` inside the guest — so the
# produced golden is byte-identical to Atlas's build-in-guest + snapshot path
# (image_recipes.py `bench-v16`). A native rewrite is a later milestone.
#
# This is the v16 line: the committed `bench.toml` already pins frappe `version-16`
# + python `3.14`, so it is uploaded as-is. Per-version bench.toml rewriting (what
# Atlas's image_builder does for v15 / nightly) is out of scope for the shim; the
# `frappe_branch` / `python_version` inputs are declared for parity but must match
# the committed bench.toml. build.sh's own env overrides (BENCH_CLI_REF,
# ERPNEXT_BRANCH) and its positional build-mode arg ARE threaded through here.
import os
import shlex

from pyinfra import host
from pyinfra.api import deploy
from pyinfra.operations import files, server

# Absolute path so the tree resolves regardless of the runner's cwd. `tree/` holds
# the committed bench source (build.sh, warm.sh, bench.toml, deploy-site.py, …);
# README.md and __pycache__ are excluded.
_TREE = os.path.join(os.path.dirname(__file__), "tree")


@deploy("build")
def build():
    files.sync(
        name="upload bench source tree to /root/bench",
        src=_TREE,
        dest="/root/bench",
        exclude=["*.pyc"],
        exclude_dir=["__pycache__", "*/__pycache__"],
        add_deploy_dir=False,
    )
    files.file(
        name="make build.sh executable",
        path="/root/bench/build.sh",
        mode="755",
    )
    files.file(
        name="make warm.sh executable",
        path="/root/bench/warm.sh",
        mode="755",
    )

    # build.sh reads BENCH_CLI_REF / ERPNEXT_BRANCH from the env and takes the build
    # mode as its positional arg (the same overrides Atlas's image_builder exports).
    build_mode = host.data.get("build_mode", "site")
    env = (
        f"BENCH_CLI_REF={shlex.quote(host.data.get('bench_cli_ref', ''))} "
        f"ERPNEXT_BRANCH={shlex.quote(host.data.get('erpnext_branch', 'version-16'))}"
    )
    server.shell(
        name="run bench build.sh",
        commands=[f"{env} bash /root/bench/build.sh {shlex.quote(build_mode)}"],
        _timeout=3600,
    )


@deploy("warm_arm")
def warm_arm():
    # Arm the running stack for a warm capture: install + start the freshen unit and
    # pre-warm with real HTTP. warm.sh takes the build VM's uuid as $1 (its
    # "identity already adopted" marker); off an Atlas bake that is /etc/atlas-vm-uuid,
    # else a harmless placeholder.
    server.shell(
        name="arm warm capture (warm.sh)",
        commands=["bash /root/bench/warm.sh $(cat /etc/atlas-vm-uuid 2>/dev/null || echo chef)"],
        _timeout=1800,
    )


@deploy("verify")
def verify():
    # A serving bench answers Frappe's ping (site mode) or the admin app's status
    # (admin mode); either way nginx should be up. Kept lenient/non-fatal — the
    # authoritative gate is build.sh's own `set -euo pipefail` — but present so a
    # dead stack is at least visible in the bake log.
    server.shell(
        name="bench responds (best-effort)",
        commands=[
            "curl -fsS -o /dev/null http://localhost/api/method/ping || "
            "curl -fsS -o /dev/null http://localhost/api/status || "
            "systemctl is-active nginx || true"
        ],
    )
