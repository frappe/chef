# recipe.py — pure pyinfra, no chef/atlas imports.
#
# Native pyinfra port of the Garage build.sh.
#
# Installs nginx + the pinned Garage release, installs the committed
# systemd unit, reloads systemd, verifies Garage, and syncs the filesystem.
#
# GARAGE_VERSION may be supplied through the environment when invoking
# pyinfra, matching build.sh's:
#
#   GARAGE_VERSION="${GARAGE_VERSION:-v2.3.0}"
#
# Architecture is detected on the guest, matching uname -m in build.sh.

import os

from pyinfra.api import deploy
from pyinfra.operations import apt, files, server


# Absolute path so the committed guest asset resolves regardless of
# the runner's current working directory.
_ROOT = os.path.dirname(os.path.abspath(__file__))

# Match:
#   GARAGE_VERSION="${GARAGE_VERSION:-v2.3.0}"
GARAGE_VERSION = os.environ.get("GARAGE_VERSION", "v2.3.0")

GARAGE_PATH = "/usr/local/bin/garage"
GARAGE_SERVICE = "/etc/systemd/system/garage.service"


@deploy("build")
def build():
    # Match:
    #   export DEBIAN_FRONTEND=noninteractive
    #   apt-get update
    #   apt-get install -y --no-install-recommends ca-certificates curl nginx
    apt.packages(
        name="install nginx + Garage download dependencies",
        packages=[
            "ca-certificates",
            "curl",
            "nginx",
        ],
        update=True,
        no_recommends=True,
        _retries=3,
        _retry_delay=10,
    )

    # Match:
    #   rm -rf /etc/nginx/sites-enabled/default
    files.delete(
        name="remove nginx default site",
        path="/etc/nginx/sites-enabled/default",
    )

    # Download the architecture-specific static Garage binary.
    #
    # build.sh maps:
    #   x86_64 / amd64 -> amd64 / x86_64
    #   aarch64 / arm64 -> arm64 / aarch64
    #
    # The mapping is deliberately performed on the guest rather than on the
    # pyinfra runner, because the target VM's architecture is what matters.
    server.shell(
        name="download Garage ${}".format(GARAGE_VERSION),
        commands=[
            r"""
set -euo pipefail

ARCH="$(uname -m)"

case "$ARCH" in
    x86_64|amd64)
        RELEASE_ARCH=x86_64
        ;;
    aarch64|arm64)
        RELEASE_ARCH=aarch64
        ;;
    *)
        echo "unsupported architecture: $ARCH" >&2
        exit 1
        ;;
esac

GARAGE_VERSION="{}"

URL="https://garagehq.deuxfleurs.fr/_releases/${GARAGE_VERSION}/${RELEASE_ARCH}-unknown-linux-musl/garage"

echo "installing Garage ${GARAGE_VERSION} for ${RELEASE_ARCH} (${GO_ARCH})"

curl -fsSL "$URL" -o "{}"
chmod 0755 "{}"
""".format(
                GARAGE_VERSION,
                GARAGE_PATH,
                GARAGE_PATH,
            )
        ],
        _shell_executable="bash",
        _timeout=300,
        _retries=3,
        _retry_delay=15,
    )

    # Match:
    #   install -m 0644 "$ROOT/guest/garage.service" \
    #       /etc/systemd/system/garage.service
    #
    # test -s below is retained as an explicit validation just like build.sh.
    files.put(
        name="install Garage systemd service",
        src=os.path.join(_ROOT, "guest", "garage.service"),
        dest=GARAGE_SERVICE,
        mode="644",
        add_deploy_dir=False,
    )

    server.shell(
        name="validate Garage service file",
        commands=[
            'test -s "{}"'.format(GARAGE_SERVICE),
        ],
        _shell_executable="bash",
    )

    # Match:
    #   systemctl daemon-reload
    server.shell(
        name="reload systemd",
        commands=[
            "systemctl daemon-reload",
        ],
        _shell_executable="bash",
    )

    # Match:
    #   /usr/local/bin/garage --version
    server.shell(
        name="verify Garage installation",
        commands=[
            '"{}" --version'.format(GARAGE_PATH),
        ],
        _shell_executable="bash",
    )

    # Match the final:
    #   sync
    #
    # Keep this as an explicit build step because the original build.sh
    # deliberately flushes the filesystem before the VM/image operation.
    server.shell(
        name="sync filesystem",
        commands=[
            "sync",
        ],
        _shell_executable="bash",
    )

    # Match:
    #   sleep 5
    #
    # This is intentionally retained even though it is not normally needed
    # by pyinfra itself; it preserves the original image-build timing.
    server.shell(
        name="wait for filesystem sync",
        commands=[
            "sleep 5",
        ],
        _shell_executable="bash",
    )
