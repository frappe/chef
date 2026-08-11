# recipe.py — pure pyinfra, no chef/atlas imports.
#
# Assumes the `frappe` user exists, the way every other Frappe recipe does — pilot's
# install.sh creates it. Compose pilot ahead of this, or bake onto an image that has it.

import os
import secrets
import shlex
from io import StringIO

from pyinfra import host
from pyinfra.api import deploy
from pyinfra.operations import apt, files, server

_TEMPLATES = os.path.join(os.path.dirname(__file__), "templates")
_UNIT = os.path.join(_TEMPLATES, "datum.service.j2")
_ENV = os.path.join(_TEMPLATES, "datum.env.j2")
_USERS_SQL = os.path.join(_TEMPLATES, "users.sql.j2")
_ACCESS = os.path.join(_TEMPLATES, "access-management.xml.j2")

SERVICE_USER = "frappe"
BASE_DIR = f"/home/{SERVICE_USER}/datum"
DEV_DIR = f"{BASE_DIR}/.dev"
ENV_FILE = f"{DEV_DIR}/datum.env"
PUBLIC_KEY_FILE = f"{DEV_DIR}/central.pub"
LOG_DIR = f"/home/{SERVICE_USER}/.local/share/datum/logs"
SYSTEMD_USER_DIR = f"/home/{SERVICE_USER}/.config/systemd/user"
UNIT_FILE = f"{SYSTEMD_USER_DIR}/datum.service"

ACCESS_FILE = "/etc/clickhouse-server/users.d/access-management.xml"
USERS_SQL_FILE = "/root/users.sql"

DATABASE = "datum"
TABLE = "samples"
UV = "/usr/local/bin/uv"
_AS_SERVICE_USER = {"_su_user": SERVICE_USER}

# `systemctl --user` needs the user's own bus, so XDG_RUNTIME_DIR has to be spelled out.
_USER_SYSTEMCTL = (
    f"sudo -u {SERVICE_USER} XDG_RUNTIME_DIR=/run/user/$(id -u {SERVICE_USER}) systemctl --user"
)

# apt-key is gone in 24.04, so the key is dearmoured and the repo line signed-by= it.
_KEY_URL = "https://packages.clickhouse.com/rpm/lts/repodata/repomd.xml.key"
_KEYRING = "/usr/share/keyrings/clickhouse-keyring.gpg"
_REPO = f"deb [signed-by={_KEYRING}] https://packages.clickhouse.com/deb stable main"


def make_password() -> str:
    return secrets.token_urlsafe(24)


def create_directories():
    for path in (BASE_DIR, DEV_DIR, LOG_DIR, SYSTEMD_USER_DIR):
        files.directory(
            name=f"ensure {path}",
            path=path,
            user=SERVICE_USER,
            group=SERVICE_USER,
            mode="750",
            present=True,
        )


def install_clickhouse():
    apt.packages(
        name="install apt prerequisites",
        packages=["apt-transport-https", "ca-certificates", "curl", "gnupg"],
        update=True,
        _retries=3,
        _retry_delay=10,
    )
    server.shell(
        name="add the ClickHouse signing key",
        commands=[
            f"curl -fsSL {_KEY_URL} | gpg --dearmor --yes -o {_KEYRING}",
            f"chmod 644 {_KEYRING}",
        ],
        _retries=3,
        _retry_delay=10,
    )
    apt.repo(name="add the ClickHouse apt repo", src=_REPO, filename="clickhouse")
    apt.packages(
        name="install clickhouse-server + clickhouse-client",
        packages=["clickhouse-server", "clickhouse-client"],
        update=True,
        _env={"DEBIAN_FRONTEND": "noninteractive"},
        _retries=3,
        _retry_delay=15,
        _timeout=900,
    )
    server.service(
        name="enable + start clickhouse-server",
        service="clickhouse-server",
        running=True,
        enabled=True,
        _retries=3,
    )


def create_clickhouse_users(datum_password: str, insights_password: str):
    # CREATE USER needs SQL-driven access control, which a stock install has off.
    files.template(
        name="enable SQL access management for default",
        src=_ACCESS,
        dest=ACCESS_FILE,
        mode="644",
    )
    server.service(
        name="restart clickhouse-server",
        service="clickhouse-server",
        restarted=True,
        _retries=3,
    )
    # Written 0600 and deleted after: pyinfra logs the commands it runs, so the
    # passwords never go on a command line.
    files.template(
        name="write the user SQL",
        src=_USERS_SQL,
        dest=USERS_SQL_FILE,
        mode="600",
        database=DATABASE,
        datum_password=datum_password,
        insights_password=insights_password,
    )
    server.shell(
        name="create the datum and insights users",
        commands=[f"clickhouse-client --queries-file {USERS_SQL_FILE}"],
        _retries=6,
        _retry_delay=5,
    )
    files.file(name="remove the user SQL", path=USERS_SQL_FILE, present=False)


def install_uv():
    server.shell(
        name="ensure uv is installed",
        commands=[
            (
                f"test -x {UV} || curl -LsSf https://astral.sh/uv/install.sh "
                "| UV_INSTALL_DIR=/usr/local/bin sh"
            )
        ],
        _retries=3,
        _retry_delay=10,
    )


def clone_datum():
    apt.packages(name="install git", packages=["git"], update=True, _retries=3)
    server.git(
        name="clone datum",
        src=host.data.get("datum_source", "https://github.com/frappe/datum.git"),
        dest=BASE_DIR,
        branch=host.data.get("datum_branch", "main"),
        pull=True,
        user=SERVICE_USER,
        group=SERVICE_USER,
        _retries=3,
        _retry_delay=10,
    )
    server.shell(
        name="uv sync",
        commands=[f"{UV} sync --frozen --directory {shlex.quote(BASE_DIR)}"],
        _timeout=900,
        _retries=2,
        _retry_delay=15,
        **_AS_SERVICE_USER,
    )


def write_env_file(datum_password: str):
    public_key = host.data.get("jwt_public_key", "")
    if public_key:
        files.put(
            name="write the JWT public key",
            src=StringIO(public_key.rstrip("\n") + "\n"),
            dest=PUBLIC_KEY_FILE,
            user=SERVICE_USER,
            group=SERVICE_USER,
            mode="640",
        )
    files.template(
        name=f"render {ENV_FILE}",
        src=_ENV,
        dest=ENV_FILE,
        user=SERVICE_USER,
        group=SERVICE_USER,
        mode="640",
        database=DATABASE,
        table=TABLE,
        datum_password=datum_password,
        jwt_public_key=public_key,
        public_key_file=PUBLIC_KEY_FILE,
        oidc_issuer=host.data.get("oidc_issuer", ""),
    )


def install_unit():
    files.template(
        name="render datum.service",
        src=_UNIT,
        dest=UNIT_FILE,
        user=SERVICE_USER,
        group=SERVICE_USER,
        mode="644",
        base_dir=BASE_DIR,
        log_dir=LOG_DIR,
        env_file=ENV_FILE,
        listen_port=host.data.get("listen_port", "8000"),
        workers=host.data.get("workers", "2"),
    )
    # Lingering starts the user manager at boot, so the unit comes up without a login.
    server.shell(
        name=f"enable lingering for {SERVICE_USER}",
        commands=[f"loginctl enable-linger {SERVICE_USER}"],
    )
    server.shell(
        name="enable + start datum",
        commands=[
            f"{_USER_SYSTEMCTL} daemon-reload",
            f"{_USER_SYSTEMCTL} enable datum.service",
            f"{_USER_SYSTEMCTL} restart datum.service",
        ],
        _retries=3,
        _retry_delay=5,
    )


def report(datum_password: str, insights_password: str):
    """Chef has no way to return a value from a bake, so the passwords go to the log."""
    server.shell(
        name=f"CREDENTIALS datum={datum_password} insights={insights_password}",
        commands=["true"],
    )


@deploy("build")
def build():
    datum_password = make_password()
    insights_password = make_password()

    create_directories()
    install_clickhouse()
    create_clickhouse_users(datum_password, insights_password)
    install_uv()
    clone_datum()
    write_env_file(datum_password)
    install_unit()
    report(datum_password, insights_password)


@deploy("verify")
def verify():
    server.shell(
        name="clickhouse answers",
        commands=["clickhouse-client --query 'SELECT 1' >/dev/null"],
        _retries=6,
        _retry_delay=5,
    )
    server.shell(
        name="both users exist",
        commands=[
            (
                "clickhouse-client --query \"SELECT count() FROM system.users "
                "WHERE name IN ('datum', 'insights')\" | grep -qx 2"
            )
        ],
    )
    server.shell(
        name="datum-api is up and enabled",
        commands=[
            f"curl -fsS http://127.0.0.1:{host.data.get('listen_port', '8000')}/health >/dev/null",
            f"{_USER_SYSTEMCTL} is-enabled datum.service",
            f"loginctl show-user {SERVICE_USER} -p Linger | grep -qx Linger=yes",
        ],
        _retries=6,
        _retry_delay=5,
    )
    server.shell(
        name="ensure_schema created the table",
        commands=[f"clickhouse-client --query 'EXISTS TABLE {DATABASE}.{TABLE}' | grep -qx 1"],
        _retries=6,
        _retry_delay=5,
    )
