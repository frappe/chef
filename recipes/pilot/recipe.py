# recipe.py — bake a Pilot golden: a working admin console + a Frappe site.
#
# Native pyinfra, no shim. "Only bakes pilot" — Frappe + one site, no ERPNext and no
# Atlas-specific bench config. Derived by rawdogging frappe/pilot v0.0.x-pre-alpha
# headlessly; the load-bearing lessons (each a step below):
#   1. install.sh is TWO invocations now: as ROOT it creates the `frappe` user + the
#      system stack + enables systemd lingering (so `systemctl --user` units survive a
#      snapshot boot); as FRAPPE it installs the pilot CLI. Running only the frappe half
#      (as Atlas's old build.sh did) leaves no lingering/XDG_RUNTIME_DIR and breaks
#      production setup.
#   2. the Ubuntu cloud rootfs ships with setuid bits stripped — restore them or `sudo`
#      fails ("must be owned by uid 0 and have the setuid bit set").
#   3. `pilot setup production` guards on `stdin.isatty()`; over SSH there is no TTY, so
#      wrap it in `script -qec … /dev/null` to fake one.
#   4. pilot's launcher advertises "stdlib only" but `setup production` imports psutil,
#      which isn't in system python3 — install python3-psutil system-wide (pilot bug).
#
# Frappe commands run as the `frappe` user through a login shell via pyinfra's
# `_su_user`/`_use_su_login` (so pilot is on PATH); root steps run as root.

from io import StringIO

from pyinfra import host
from pyinfra.api import deploy
from pyinfra.operations import apt, files, server

_INSTALL_URL = "https://raw.githubusercontent.com/frappe/pilot/develop/install.sh"
_BENCH = "atlas"
_BENCH_DIR = f"/home/frappe/pilot/benches/{_BENCH}"

# run a command as the frappe user through a login shell
_AS_FRAPPE = {"_su_user": "frappe", "_use_su_login": True}


@deploy("build pilot golden")
def build():
    admin_pw = host.data.get("admin_password", "admin123")
    site = host.data.get("site_name", "site.local")
    admin_domain = host.data.get("admin_domain", "admin.local")
    frappe_branch = host.data.get("frappe_branch", "version-16")

    # 1. restore the setuid bits the cloud rootfs strips (sudo/su/passwd/… need them).
    server.shell(
        name="restore setuid bits",
        commands=[
            "chmod u+s /usr/bin/sudo /usr/bin/passwd /usr/bin/su /bin/su "
            "/usr/bin/chsh /usr/bin/newgrp /usr/bin/mount /bin/mount 2>/dev/null || true"
        ],
    )

    # 2. pilot's launcher claims stdlib-only, but `setup production` imports psutil from
    #    system python3 — install it system-wide (pilot pre-alpha workaround).
    apt.packages(
        name="install python3-psutil (pilot setup-production needs it in system python3)",
        packages=["python3-psutil"],
        update=True,
        _retries=3,
        _retry_delay=10,
    )

    # 3. install.sh as ROOT — frappe user + system deps + production stack + lingering.
    server.shell(
        name="pilot install.sh as root (frappe user + system stack + lingering)",
        commands=[f"curl -fsSL {_INSTALL_URL} | bash"],
        _timeout=1200,
        _retries=2,
        _retry_delay=15,
    )

    # 4. install.sh as FRAPPE — the pilot CLI onto the frappe user's PATH.
    server.shell(
        name="pilot install.sh as frappe (the pilot CLI)",
        commands=[f"curl -fsSL {_INSTALL_URL} | bash"],
        _timeout=600,
        _retries=2,
        _retry_delay=15,
        **_AS_FRAPPE,
    )

    # 5. `setup production` sudoes for nginx/systemd; grant frappe passwordless sudo so it
    #    runs headless. (Convenient for a dev golden; narrow this for real production.)
    files.put(
        name="grant frappe passwordless sudo (headless setup production)",
        src=StringIO("frappe ALL=(ALL) NOPASSWD:ALL\n"),
        dest="/etc/sudoers.d/frappe-bake",
        mode="440",
    )

    # 6. create the bench, pin the admin password + the frappe branch in bench.toml.
    server.shell(
        name="pilot new + configure bench.toml",
        commands=[
            f"test -d {_BENCH_DIR} || pilot new {_BENCH}",
            f'cd {_BENCH_DIR} && sed -i \'s|^password = ""|password = "{admin_pw}"|\' bench.toml',
            f'cd {_BENCH_DIR} && sed -i \'s|^branch = "version-16"|branch = "{frappe_branch}"|\' bench.toml',
        ],
        _timeout=120,
        **_AS_FRAPPE,
    )

    # 7. init the bench — clones frappe + builds its assets (the heavy, fat-memory step).
    server.shell(
        name="pilot init (build frappe)",
        commands=[f"pilot -b {_BENCH} init"],
        _timeout=2400,
        _retries=2,
        _retry_delay=20,
        **_AS_FRAPPE,
    )

    # 8. create the frappe site.
    server.shell(
        name=f"pilot new-site {site}",
        commands=[
            f"test -d {_BENCH_DIR}/sites/{site} || "
            f"pilot -b {_BENCH} new-site {site} --admin-password {admin_pw}"
        ],
        _timeout=1200,
        _retries=2,
        _retry_delay=20,
        **_AS_FRAPPE,
    )

    # 9. bring up production (systemd --user units + nginx). Fake a TTY via `script`.
    server.shell(
        name="pilot setup production (systemd + nginx)",
        commands=[
            f'script -qec "pilot -b {_BENCH} setup production --admin-domain {admin_domain}" /dev/null'
        ],
        _timeout=1200,
        _retries=2,
        _retry_delay=20,
        **_AS_FRAPPE,
    )


@deploy("verify pilot golden")
def verify():
    site = host.data.get("site_name", "site.local")
    admin_domain = host.data.get("admin_domain", "admin.local")
    server.shell(
        name="admin console + frappe site serve (via nginx)",
        commands=[
            # the admin console answers 200 with the Pilot title
            f"curl -fsS -H 'Host: {admin_domain}' http://localhost/ | grep -qi '<title>Pilot' "
            "&& echo ADMIN_OK",
            # the frappe site answers ping with pong
            f"curl -fsS -H 'Host: {site}' http://localhost/api/method/ping | grep -q pong "
            "&& echo SITE_OK",
        ],
    )
