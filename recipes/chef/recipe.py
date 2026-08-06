# recipe.py — pure pyinfra, no chef/atlas imports.
#
# "chef is itself a recipe" (decision #9): installing chef on a machine is just baking
# the `chef` recipe. Run via `chef install-service` it targets pyinfra's @local connector
# (base_image/size are ignored — they only matter when a Builder bakes a chef *image*).
import os
import shlex
from io import StringIO

from pyinfra import host
from pyinfra.api import deploy
from pyinfra.facts.server import Which
from pyinfra.operations import apt, files, server

# Absolute paths so the templates resolve regardless of the runner's cwd.
_TEMPLATES = os.path.join(os.path.dirname(__file__), "templates")
_API_UNIT = os.path.join(_TEMPLATES, "chef-api.service.j2")
_WORKER_UNIT = os.path.join(_TEMPLATES, "chef-worker.service.j2")

# uv and `uv tool install` drop binaries under ~/.local/bin; the units ExecStart these.
_UV = "$HOME/.local/bin/uv"
_CHEF_BIN = "/root/.local/bin/chef"


@deploy("build")
def build():
    # apt/systemd may be absent when authoring against a bare @local — gate the ops that
    # need them so the recipe degrades to a no-op instead of failing loud.
    has_apt = host.get_fact(Which, command="apt-get")
    has_systemctl = host.get_fact(Which, command="systemctl")

    # 1. uv — the standard installer, guarded so a second run is a no-op.
    server.shell(
        name="ensure uv is installed",
        commands=[
            "command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh"
        ],
        _retries=3,
        _retry_delay=10,
    )

    # 2. redis — the api ↔ worker log bus + job queue.
    if has_apt:
        apt.packages(
            name="install redis-server",
            packages=["redis-server"],
            update=True,
            _retries=3,
            _retry_delay=10,
        )
    if has_systemctl:
        server.service(
            name="enable + start redis-server",
            service="redis-server",
            running=True,
            enabled=True,
            _retries=3,
        )

    # 3. chef itself — `uv tool install` lands the `chef` CLI on ~/.local/bin. --force keeps
    # it deterministic (reinstall/upgrade) rather than depending on a prior state.
    chef_source = host.data.get("chef_source", "git+https://github.com/frappe/chef")
    server.shell(
        name="uv tool install chef",
        commands=[f"{_UV} tool install --force {shlex.quote(chef_source)}"],
        _retries=3,
        _retry_delay=10,
    )

    # 4. config — a single EnvironmentFile shared by both units.
    redis_url = host.data.get("redis_url", "redis://localhost:6379")
    files.directory(name="ensure /etc/chef", path="/etc/chef", present=True)
    files.put(
        name="write /etc/chef/chef.env",
        src=StringIO(f"CHEF_REDIS_URL={redis_url}\n"),
        dest="/etc/chef/chef.env",
        mode="640",
    )

    # 5. systemd units — ExecStart the installed CLI, EnvironmentFile the config above.
    files.template(
        name="render chef-api.service",
        src=_API_UNIT,
        dest="/etc/systemd/system/chef-api.service",
        chef_bin=_CHEF_BIN,
    )
    files.template(
        name="render chef-worker.service",
        src=_WORKER_UNIT,
        dest="/etc/systemd/system/chef-worker.service",
        chef_bin=_CHEF_BIN,
    )

    # 6. reload + enable/start both units.
    if has_systemctl:
        server.shell(
            name="systemctl daemon-reload",
            commands=["systemctl daemon-reload"],
        )
        server.service(
            name="enable + start chef-api",
            service="chef-api",
            running=True,
            enabled=True,
            _retries=3,
        )
        server.service(
            name="enable + start chef-worker",
            service="chef-worker",
            running=True,
            enabled=True,
            _retries=3,
        )


@deploy("verify")
def verify():
    server.shell(
        name="chef CLI reachable + units enabled",
        commands=[
            "chef --help >/dev/null",
            "systemctl is-enabled chef-api chef-worker || true",
        ],
    )
