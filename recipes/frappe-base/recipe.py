# recipe.py — install all 9 first-party Frappe apps onto the Pilot golden, then DISABLE them
# so only their DB schema (tables/doctypes) remains in the baked base image; the app code is
# inert (removed from the site's active apps via frappe's disable-app, PR frappe/frappe#41563).
#
# compose = ["pilot"], so chef runs pilot's whole build first (bench `atlas` + site
# `site.local`), then these ops on the same VM. Frappe steps run as the `frappe` user through
# a login shell (so `pilot` is on PATH) via pyinfra's `_su_user`/`_use_su_login`. `disable-app`
# and `migrate` are reached through pilot's frappe passthrough:
# `pilot -b atlas frappe --site <site> <cmd>` == `bench --site <site> <cmd>`.
#
# INSTALL order is dependency-correct (erpnext before its dependents hrms/lending); DISABLE
# order is the reverse, because frappe refuses to disable an app another ACTIVE app depends on
# ("App X is a dependency of Y. Disable Y first."), so erpnext is disabled last.

from io import StringIO
from pathlib import Path

from pyinfra import host
from pyinfra.api import deploy
from pyinfra.operations import files, server

_BENCH = "atlas"

# run a command as the frappe user through a login shell (same as the pilot recipe)
_AS_FRAPPE = {"_su_user": "frappe", "_use_su_login": True}

# (frappe app name, git repo) — install order: erpnext first (hrms & lending depend on it).
_APPS = [
    ("erpnext", "https://github.com/frappe/erpnext"),
    ("hrms", "https://github.com/frappe/hrms"),
    ("lending", "https://github.com/frappe/lending"),
    ("crm", "https://github.com/frappe/crm"),
    ("helpdesk", "https://github.com/frappe/helpdesk"),
    ("lms", "https://github.com/frappe/lms"),
    ("gameplan", "https://github.com/frappe/gameplan"),
    ("builder", "https://github.com/frappe/builder"),
    ("insights", "https://github.com/frappe/insights"),
]

# The frappe app branch (develop) — read from host.data so it tracks the pinned frappe branch.
_APP_BRANCH = "develop"

_VERIFY_DEST = "/home/frappe/verify_base.py"


@deploy("install + disable the 9 frappe apps")
def build():
    site = host.data.get("site_name", "site.local")

    # 1. install each app onto pilot's bench + site, in dependency order. get-app clones the
    #    repo, pip-installs it into the bench env, and builds its assets (the fat step);
    #    install-app materializes its schema on the site.
    for app, repo in _APPS:
        server.shell(
            name=f"get-app {app} ({_APP_BRANCH})",
            commands=[f"pilot -b {_BENCH} get-app {repo} --branch {_APP_BRANCH}"],
            _timeout=3600,
            _retries=2,
            _retry_delay=20,
            **_AS_FRAPPE,
        )
        server.shell(
            name=f"install-app {app} on {site}",
            commands=[f"pilot -b {_BENCH} install-app {site} {app}"],
            _timeout=1800,
            _retries=2,
            _retry_delay=20,
            **_AS_FRAPPE,
        )

    # 2. migrate to materialize/patch all schema while every app is still active.
    server.shell(
        name=f"migrate {site} (materialize all schema)",
        commands=[f"pilot -b {_BENCH} frappe --site {site} migrate"],
        _timeout=3600,
        _retries=2,
        _retry_delay=20,
        **_AS_FRAPPE,
    )

    # 3. DISABLE each app (keeps schema + data, makes code inert), in REVERSE dependency order
    #    so a dependency is never disabled while an active dependent still needs it.
    for app, _repo in reversed(_APPS):
        server.shell(
            name=f"disable-app {app}",
            commands=[f"pilot -b {_BENCH} frappe --site {site} disable-app {app}"],
            _timeout=600,
            _retries=2,
            _retry_delay=15,
            **_AS_FRAPPE,
        )


@deploy("verify all 9 apps: schema present AND disabled")
def verify():
    site = host.data.get("site_name", "site.local")
    bench_dir = f"/home/frappe/pilot/benches/{_BENCH}"

    # upload the verifier that asserts, for each app: installed (schema materialized), disabled
    # (in the disabled_apps global), NOT active (code inert), and its doctype tables exist.
    files.put(
        name="upload verify_base.py",
        src=StringIO((Path(__file__).parent / "files" / "verify_base.py").read_text()),
        dest=_VERIFY_DEST,
        mode="644",
    )

    # run it in the bench env, from the sites dir (frappe.init needs that cwd).
    server.shell(
        name="all 9 apps: tables present AND disabled",
        commands=[
            (
                f"cd {bench_dir}/sites && "
                f"{bench_dir}/env/bin/python {_VERIFY_DEST} {site}"
            ),
        ],
        _timeout=600,
        **_AS_FRAPPE,
    )
