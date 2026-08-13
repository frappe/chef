# recipe.py — bake a Pilot golden: a working admin console + a Frappe site.
#
# Native pyinfra, no shim. "Only bakes pilot" — Frappe + one site, no ERPNext and no
# Atlas-specific bench config. Derived by rawdogging frappe/pilot v0.0.x-pre-alpha
# headlessly; the load-bearing lessons (each a step below):
#   1. install.sh is TWO invocations now: as ROOT it creates the `frappe` user + the
#      system stack + enables systemd lingering (so `systemctl --user` units survive a
#      snapshot boot); as FRAPPE it installs pilot itself. Running only the frappe half
#      (as Atlas's old build.sh did) leaves no lingering/XDG_RUNTIME_DIR and breaks
#      production setup. We keep the ROOT half but replace the FRAPPE half: install.sh's
#      default fetch grabs the *latest* release (unpinnable), so to bake a specific,
#      reproducible pilot **release** we install the pinned tag's release tarball directly
#      (mirroring install.sh's `install_for_user`). The pinned ref comes from the
#      release-tracking store via `host.data["chef_releases"]["frappe/pilot"]`.
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
_PILOT_REPO = "frappe/pilot"            # the repo whose release this recipe pins ([[track]])
_BENCH = "atlas"
_BENCH_DIR = f"/home/frappe/pilot/benches/{_BENCH}"

# run a command as the frappe user through a login shell
_AS_FRAPPE = {"_su_user": "frappe", "_use_su_login": True}


def _pinned_install_script(ref: str) -> str:
    """Install a specific pilot release from its prebuilt tarball, as the frappe user.

    A faithful, pinned stand-in for install.sh's ``install_for_user``: it fetches the
    tagged ``pilot.tar.gz`` release asset (instead of install.sh's unpinnable "latest"
    fetch), then reproduces the same uv / PATH / admin-venv setup. The ROOT half of
    install.sh (system stack, frappe user, lingering) still runs unchanged in step 3.
    """
    tarball = f"https://github.com/{_PILOT_REPO}/releases/download/{ref}/pilot.tar.gz"
    return f"""set -eu
PILOT_DIR="$HOME/pilot"
# --- pinned fetch: the tagged release tarball, extracted fresh (no 'latest' contamination)
mkdir -p "$PILOT_DIR"
tmp="$(mktemp)"
curl -fsSL --proto '=https' --tlsv1.2 "{tarball}" -o "$tmp"
tar -xzf "$tmp" -C "$PILOT_DIR"
rm -f "$tmp" "$PILOT_DIR/bench"
chmod +x "$PILOT_DIR/bin/pilot"
# --- uv (mirrors install.sh ensure_uv)
command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PILOT_DIR/bin:$PATH"
# --- pilot on PATH for future login shells (later steps run via `su - frappe`)
for rc in "$HOME/.profile" "$HOME/.bashrc"; do
  grep -qF 'pilot/bin' "$rc" 2>/dev/null || echo 'export PATH="$HOME/pilot/bin:$PATH"' >> "$rc"
done
# --- admin venv (mirrors install.sh ensure_admin_venv)
venv="$PILOT_DIR/.admin-venv"
if [ ! -f "$venv/bin/python" ]; then
  uv venv "$venv" --quiet
  deps="$(python3 -c "import tomllib;d=tomllib.load(open('$PILOT_DIR/pyproject.toml','rb'));print(' '.join(d.get('project',{{}}).get('optional-dependencies',{{}}).get('admin',[])))" 2>/dev/null || true)"
  [ -z "$deps" ] && deps="flask>=3.0 psutil>=5.9 pymysql>=1.1 gunicorn>=21.2 pyjwt[crypto]>=2.8"
  uv pip install --python "$venv/bin/python" --quiet $deps
fi
echo "pilot {ref} installed to $PILOT_DIR"
"""


@deploy("build pilot golden")
def build():
    admin_pw = host.data.get("admin_password", "admin123")
    site = host.data.get("site_name", "site.local")
    admin_domain = host.data.get("admin_domain", "admin.local")
    frappe_branch = host.data.get("frappe_branch", "version-16")

    # The pinned pilot release, resolved from the release-tracking store by the pipeline.
    pin = host.data.get("chef_releases", {}).get(_PILOT_REPO, {})
    pilot_ref = pin.get("ref")
    if not pilot_ref:
        raise RuntimeError(
            f"pilot recipe needs a pinned release for {_PILOT_REPO} — none found in "
            f"host.data['chef_releases']. Pin one: `chef releases set {_PILOT_REPO} <tag>`."
        )

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

    # 4. install the PINNED pilot release as FRAPPE (release tarball, not install.sh's
    #    unpinnable "latest") — reproduces install.sh's install_for_user around that fetch.
    server.shell(
        name=f"install pinned pilot {pilot_ref} (release tarball)",
        commands=[_pinned_install_script(pilot_ref)],
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

    # 10. `setup production` starts + configures nginx but leaves it DISABLED, so a
    #     cold-booted clone never starts the reverse proxy (the frappe stack's own
    #     systemd --user units DO come up via lingering). Enable it. Root step.
    server.shell(
        name="enable nginx on boot",
        commands=["systemctl enable nginx"],
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
