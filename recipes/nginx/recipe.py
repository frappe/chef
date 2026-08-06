# recipe.py — pure pyinfra, no chef/atlas imports.
import os

from pyinfra import host
from pyinfra.api import deploy
from pyinfra.operations import apt, files, server

# Absolute path so the template resolves regardless of the runner's cwd.
_NGINX_CONF = os.path.join(os.path.dirname(__file__), "templates", "nginx.conf.j2")


@deploy("build")
def build():
    apt.packages(
        name="install nginx",
        packages=["nginx"],
        update=True,
        _retries=3,
        _retry_delay=10,
    )
    files.template(
        name="render nginx.conf",
        src=_NGINX_CONF,
        dest="/etc/nginx/nginx.conf",
        worker_processes=host.data.get("worker_processes", "auto"),
    )
    server.service(
        name="enable + start nginx",
        service="nginx",
        running=True,
        enabled=True,
    )


@deploy("verify")
def verify():
    server.shell(
        name="curl localhost returns 200",
        commands=["curl -fsS http://localhost/ >/dev/null"],
    )
