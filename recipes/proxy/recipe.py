# recipe.py — pure pyinfra, no chef/atlas imports.
#
# SHIM recipe (plan decision #20): rather than reimplement the proxy stack in
# native pyinfra ops, this uploads Atlas's committed `proxy/` source tree verbatim
# and runs its existing `build.sh` inside the guest — so the produced golden is
# byte-identical to Atlas's own build-in-guest + snapshot path (image_recipes.py
# `proxy`). A native rewrite is a later milestone.
import os

from pyinfra.api import deploy
from pyinfra.operations import files, server

# Absolute path so the tree resolves regardless of the runner's cwd. `tree/` holds
# the committed proxy source (build.sh + conf/lua/html/guest/patches); test/ and
# README.md are excluded, mirroring image_recipes.py `exclude=("test",)`.
_TREE = os.path.join(os.path.dirname(__file__), "tree")


@deploy("build")
def build():
    files.sync(
        name="upload proxy source tree to /root/proxy",
        src=_TREE,
        dest="/root/proxy",
        exclude=["*.pyc"],
        exclude_dir=["__pycache__", "*/__pycache__"],
        add_deploy_dir=False,
    )
    files.file(
        name="make build.sh executable",
        path="/root/proxy/build.sh",
        mode="755",
    )
    server.shell(
        name="run proxy build.sh",
        commands=["bash /root/proxy/build.sh"],
        _timeout=1800,
    )


@deploy("verify")
def verify():
    # The proxy serves specific vhosts, so a bare curl to / may legitimately 404 —
    # assert nginx is running with a valid config, not a 200. The curl is a best-effort
    # liveness touch that never fails the gate (`; true`).
    server.shell(
        name="nginx config valid + service active",
        commands=[
            "nginx -t && systemctl is-active nginx && "
            "{ curl -fsS -o /dev/null http://localhost/ || "
            "curl -fsS -o /dev/null http://localhost:80/ ; true ; }"
        ],
    )
