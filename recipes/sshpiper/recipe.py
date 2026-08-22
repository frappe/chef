"""Native pyinfra recipe for the Atlas SSH Piper stack.

Builds the committed atlas-sshpiper Go program from the recipe's
atlas-sshpiper/ directory, installs the pinned upstream sshpiperd release,
and installs the committed systemd/sshd configuration.

There is no build.sh shim: all recipe inputs are either committed under
this recipe directory or fetched from their explicitly pinned upstream
release URLs.
"""

from __future__ import annotations

import os

from pyinfra.api import deploy
from pyinfra.operations import apt, files, server


_ROOT = os.path.dirname(os.path.abspath(__file__))

SSHPIPER_VERSION = os.environ.get("SSHPIPER_VERSION", "v1.5.4")
GO_VERSION = os.environ.get("GO_VERSION", "1.26.4")

GO_TARBALL = "/tmp/go.tgz"
SSHPIPER_TARBALL = "/tmp/sshpiper.tgz"
SSHPIPER_RELEASE_DIR = "/tmp/sshpiper-release"

GO_DIR = "/usr/local/go"
ATLAS_SSH_PIPER = "/usr/local/bin/sshpiper-atlas"
SSHPIPERD = "/usr/local/bin/sshpiperd"

SERVICE_FILE = "/etc/systemd/system/sshpiper.service"
SSHD_CONFIG = "/etc/ssh/sshd_config.d/60-atlas-sshpiper.conf"


@deploy("build")
def build():
    # Match:
    #   export DEBIAN_FRONTEND=noninteractive
    #
    #   apt-get update
    #   apt-get install -y --no-install-recommends \
    #       ca-certificates curl openssh-server tar
    apt.packages(
        name="install SSH Piper build dependencies",
        packages=[
            "ca-certificates",
            "curl",
            "openssh-server",
            "tar",
        ],
        update=True,
        no_recommends=True,
        _retries=3,
        _retry_delay=10,
    )

    # The Go source tree is part of the recipe rather than an external
    # build.sh tree. Upload it to a temporary build directory in the guest.
    #
    # This includes the sshpiper.crypto submodule because it lives inside
    # atlas-sshpiper/ in the new repository layout.
    files.sync(
        name="upload atlas-sshpiper Go source",
        src=os.path.join(_ROOT, "atlas-sshpiper"),
        dest="/tmp/atlas-sshpiper",
        mode="755",
        add_deploy_dir=False,
    )

    # Determine the guest architecture and download the matching Go toolchain.
    #
    # Match:
    #   ARCH="$(uname -m)"
    #
    #   x86_64|amd64       -> amd64
    #   aarch64|arm64      -> arm64
    server.shell(
        name="install pinned Go toolchain",
        commands=[
            r"""
set -euo pipefail

GO_VERSION="{}"

ARCH="$(uname -m)"

case "$ARCH" in
    x86_64|amd64)
        GO_ARCH=amd64
        ;;
    aarch64|arm64)
        GO_ARCH=arm64
        ;;
    *)
        echo "unsupported architecture: $ARCH" >&2
        exit 1
        ;;
esac

URL="https://go.dev/dl/go${GO_VERSION}.linux-${GO_ARCH}.tar.gz"

echo "installing Go ${GO_VERSION} for ${GO_ARCH}"

curl -fsSL "$URL" -o "{}"

rm -rf "{}"
tar -C /usr/local -xzf "{}"

test -x "{}/bin/go"
""".format(
                GO_VERSION,
                GO_TARBALL,
                GO_DIR,
                GO_TARBALL,
                GO_DIR,
            )
        ],
        _shell_executable="bash",
        _timeout=300,
        _retries=3,
        _retry_delay=15,
    )

    # Match:
    #   PATH="/usr/local/go/bin:$PATH" \
    #       go -C "$ROOT" build -trimpath \
    #       -o /usr/local/bin/sshpiper-atlas .
    #
    # The source root is now /tmp/atlas-sshpiper.
    server.shell(
        name="build atlas-sshpiper",
        commands=[
            r"""
set -euo pipefail

export PATH="/usr/local/go/bin:$PATH"

go -C /tmp/atlas-sshpiper build \
    -trimpath \
    -o "{}" \
    .
""".format(ATLAS_SSH_PIPER)
        ],
        _shell_executable="bash",
        _timeout=900,
    )

    # Match the upstream sshpiperd release download.
    #
    # The release archive is architecture-specific:
    #   x86_64  -> x86_64
    #   arm64   -> aarch64
    server.shell(
        name="download pinned sshpiperd release",
        commands=[
            r"""
set -euo pipefail

SSHPIPER_VERSION="{}"

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

URL="https://github.com/tg123/sshpiper/releases/download/${SSHPIPER_VERSION}/sshpiperd_with_plugins_linux_${RELEASE_ARCH}.tar.gz"

echo "installing sshpiperd ${SSHPIPER_VERSION} for ${RELEASE_ARCH}"

curl -fsSL "$URL" -o "{}"

rm -rf "{}"
mkdir -p "{}"
tar -C "{}" -xzf "{}"

test -f "{}/sshpiperd"
install -m 0755 "{}/sshpiperd" "{}"
""".format(
                SSHPIPER_VERSION,
                SSHPIPER_TARBALL,
                SSHPIPER_RELEASE_DIR,
                SSHPIPER_RELEASE_DIR,
                SSHPIPER_RELEASE_DIR,
                SSHPIPER_TARBALL,
                SSHPIPER_RELEASE_DIR,
                SSHPIPER_RELEASE_DIR,
                SSHPIPERD,
            )
        ],
        _shell_executable="bash",
        _timeout=300,
        _retries=3,
        _retry_delay=15,
    )

    # Match:
    #   install -d -m 0700 /etc/atlas
    files.directory(
        name="create Atlas configuration directory",
        path="/etc/atlas",
        mode="0700",
    )

    # Match:
    #   install -m 0644 "$ROOT/guest/sshpiper.service" \
    #       /etc/systemd/system/sshpiper.service
    files.put(
        name="install sshpiper systemd service",
        src=os.path.join(_ROOT, "guest", "sshpiper.service"),
        dest=SERVICE_FILE,
        mode="0644",
        add_deploy_dir=False,
    )

    # Match:
    #   install -d -m 0755 /etc/ssh/sshd_config.d
    files.directory(
        name="create sshd configuration directory",
        path="/etc/ssh/sshd_config.d",
        mode="0755",
    )

    # Match:
    #   install -m 0644 "$ROOT/guest/60-atlas-sshpiper.conf" \
    #       /etc/ssh/sshd_config.d/60-atlas-sshpiper.conf
    files.put(
        name="install sshd Atlas configuration",
        src=os.path.join(_ROOT, "guest", "60-atlas-sshpiper.conf"),
        dest=SSHD_CONFIG,
        mode="0644",
        add_deploy_dir=False,
    )

    # Match:
    #   test -s /etc/systemd/system/sshpiper.service
    #   test -s /etc/ssh/sshd_config.d/60-atlas-sshpiper.conf
    server.shell(
        name="validate sshpiper configuration files",
        commands=[
            'test -s "{}"'.format(SERVICE_FILE),
            'test -s "{}"'.format(SSHD_CONFIG),
        ],
        _shell_executable="bash",
    )

    # Match:
    #   test "$(sshd -T | awk '$1 == "port" { print $2; exit }')" = "222"
    #
    # Keep this exact validation because the sshd port is part of the image
    # contract.
    server.shell(
        name="verify sshd listens on port 222",
        commands=[
            r'''test "$(sshd -T | awk '$1 == "port" { print $2; exit }')" = "222"'''
        ],
        _shell_executable="bash",
    )

    # Match:
    #   sshd -t
    server.shell(
        name="validate sshd configuration",
        commands=[
            "sshd -t",
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
    #   systemctl disable --now ssh.socket ssh.service
    #   systemctl enable --now ssh.service
    #
    # ssh.socket must be disabled because socket activation would otherwise
    # compete with the explicitly managed ssh.service.
    server.shell(
        name="enable Atlas-managed SSH service",
        commands=[
            "systemctl disable --now ssh.socket ssh.service",
            "systemctl enable --now ssh.service",
        ],
        _shell_executable="bash",
        _timeout=120,
    )

    # Match:
    #   /usr/local/bin/sshpiperd --version
    server.shell(
        name="verify sshpiperd installation",
        commands=[
            '"{}" --version'.format(SSHPIPERD),
        ],
        _shell_executable="bash",
    )

    # Match:
    #   /usr/local/bin/sshpiper-atlas --help >/dev/null
    server.shell(
        name="verify atlas-sshpiper binary",
        commands=[
            '"{}" --help >/dev/null'.format(ATLAS_SSH_PIPER),
        ],
        _shell_executable="bash",
    )

    # The original build intentionally removes the temporary compiler,
    # source archive, release archive, and extracted release.
    server.shell(
        name="remove temporary Go toolchain and build artifacts",
        commands=[
            "rm -rf '{}' '{}' '{}' '{}'".format(
                GO_TARBALL,
                SSHPIPER_TARBALL,
                SSHPIPER_RELEASE_DIR,
                "/tmp/atlas-sshpiper",
            ),
            "rm -rf '{}'".format(GO_DIR),
        ],
        _shell_executable="bash",
    )

    # Match the image-build flush.
    server.shell(
        name="sync filesystem",
        commands=[
            "sync",
        ],
        _shell_executable="bash",
    )

    # Match the original build.sh's deliberate delay after sync.
    server.shell(
        name="wait for filesystem sync",
        commands=[
            "sleep 5",
        ],
        _shell_executable="bash",
    )
