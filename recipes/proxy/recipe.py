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
    # The proxy binds a large range of listen ports, so `nginx -t` needs a high
    # open-file limit — the systemd unit sets LimitNOFILE, but an ad-hoc shell does
    # not (a default 1024 fails with EMFILE). A passing `nginx -t` under a raised
    # ulimit proves the config AND that every dynamically-compiled module loads.
    # The service is *enabled* (it starts on boot; Atlas's finalize starts it in the
    # golden) rather than necessarily active in the build VM.
    server.shell(
        name="proxy config valid (raised ulimit) + service enabled",
        commands=[
            "sh -c 'ulimit -n 1048576 2>/dev/null || ulimit -n 524288 2>/dev/null "
            "|| ulimit -n 65535; nginx -t'",
            "systemctl is-enabled nginx",
        ],
    )
