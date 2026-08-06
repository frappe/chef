# recipe.py — add ERPNext (and optionally HRMS) to a Pilot golden.
#
# This recipe `compose = ["pilot"]`, so chef runs pilot's whole build first — creating the
# `atlas` bench and the Frappe site — and THEN runs the ops below, on the same VM. We reuse
# pilot's conventions: the bench is named `atlas`, and Frappe commands run as the `frappe`
# user through a login shell (so the `pilot` CLI is on PATH) via pyinfra's `_su_user`/
# `_use_su_login`. `site_name` is read straight from host.data — it's pilot's input, unioned
# into this recipe's schema by the composition, so both recipes see the same value.
#
# The get-app/install-app verbs mirror `bench`, which pilot wraps.

from pyinfra import host
from pyinfra.api import deploy
from pyinfra.operations import server

_BENCH = "atlas"

# run a command as the frappe user through a login shell (same as the pilot recipe)
_AS_FRAPPE = {"_su_user": "frappe", "_use_su_login": True}


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


@deploy("install erpnext + hrms")
def build():
    site = host.data.get("site_name", "site.local")
    erpnext_branch = host.data.get("erpnext_branch", "version-15")
    install_hrms = _truthy(host.data.get("install_hrms", "true"))

    # 1. fetch ERPNext into pilot's bench (clones + builds its assets — the fat-memory step).
    server.shell(
        name=f"get-app erpnext ({erpnext_branch})",
        commands=[f"pilot -b {_BENCH} get-app erpnext --branch {erpnext_branch}"],
        _timeout=3600,
        _retries=2,
        _retry_delay=20,
        **_AS_FRAPPE,
    )

    # 2. install ERPNext onto the site pilot created.
    server.shell(
        name=f"install erpnext on {site}",
        commands=[f"pilot -b {_BENCH} install-app erpnext --site {site}"],
        _timeout=1800,
        _retries=2,
        _retry_delay=20,
        **_AS_FRAPPE,
    )

    # 3. HRMS builds on ERPNext; add it when requested.
    if install_hrms:
        server.shell(
            name="get-app hrms",
            commands=[f"pilot -b {_BENCH} get-app hrms"],
            _timeout=1800,
            _retries=2,
            _retry_delay=20,
            **_AS_FRAPPE,
        )
        server.shell(
            name=f"install hrms on {site}",
            commands=[f"pilot -b {_BENCH} install-app hrms --site {site}"],
            _timeout=1200,
            _retries=2,
            _retry_delay=20,
            **_AS_FRAPPE,
        )


@deploy("verify erpnext")
def verify():
    site = host.data.get("site_name", "site.local")
    server.shell(
        name="erpnext is installed on the site",
        commands=[
            f"pilot -b {_BENCH} list-apps --site {site} | grep -qi erpnext && echo ERPNEXT_OK"
        ],
        **_AS_FRAPPE,
    )
