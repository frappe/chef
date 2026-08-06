# recipe.py — pure pyinfra, no chef/atlas imports.
#
# NATIVE port of Atlas's proxy `build.sh` (proxy-design.md §3.1). Builds the Atlas
# reverse-proxy stack in the guest: stock nginx from the official nginx.org apt repo
# (EXACT-pinned) + ONLY the modules apt cannot supply — OpenResty luajit2 and the
# lua-nginx / stream-lua / headers-more dynamic modules — compiled against that exact
# nginx source, plus a one-line ssl_preread patch that lives in nginx CORE (so the apt
# binary is replaced by a same-version patched recompile). Installs the committed
# conf/lua/html/guest assets at the stock nginx paths, lays down a placeholder cert +
# runtime dirs, drops a thin systemd unit override, and enables nginx. The built VM is
# snapshotted by Atlas — that snapshot is the reusable "proxy image".
#
# This stack is ABI-bound: a dynamic module is compiled against the EXACT nginx it
# loads into, and lua-resty-core asserts EXACT subsystem versions at startup. So every
# pin below is load-bearing — bumping any one is a coordinated stack update rolled as a
# new snapshot, and each section fails LOUD (installed-version assert; the recompile
# `--add-dynamic-module=.*stream-lua` assert; `nginx -t` under a raised ulimit). The
# pins/flags/URLs/`-subj` here are carried over verbatim from the authoritative build.sh.
import os

from pyinfra.api import deploy
from pyinfra.operations import apt, files, server

# Absolute path so the committed assets resolve regardless of the runner's cwd.
_FILES = os.path.join(os.path.dirname(__file__), "files")

# --- Pinned versions (proxy-design.md §3.1). Copied byte-for-byte from build.sh. The
# nginx BASE is pinned to an exact nginx.org package version because the dynamic modules
# are compiled against this exact nginx source; STREAM_LUA_MODULE_REF is NOT free to pick
# — lua-resty-core 0.1.32 asserts ngx_stream_lua_module == 0.0.17 (and http-lua ==
# 0.10.29) at startup. Everything the binary is made of is pinned, so two bakes a year
# apart produce the same stack. ---
NGINX_VERSION = "1.30.3"  # nginx.org STABLE (even minor); base binary + OpenSSL
NGINX_PKG_RELEASE = "1"  # the "-N~<codename>" deb revision
LUAJIT2_REF = "v2.1-20250529"  # OpenResty's fork (NOT upstream LuaJIT)
LUA_NGINX_MODULE_VERSION = "0.10.29"
STREAM_LUA_MODULE_REF = "v0.0.17"  # locked to the resty-core + lua-nginx set
NDK_VERSION = "0.3.4"  # ngx_devel_kit — MUST precede both lua modules
LUA_RESTY_CORE_VERSION = "0.1.32"  # mandatory — nginx won't start without it
LUA_RESTY_LRUCACHE_VERSION = "0.15"  # dependency of lua-resty-core
LUA_CJSON_VERSION = "2.1.0.14"  # cjson C module — NOT bundled with vanilla nginx
HEADERS_MORE_VERSION = "0.39"  # more_set_headers

# The pinned versions, rendered as the bash `NAME="value"` preamble each section reads.
# Generated FROM the Python constants above, so the shell vars can never drift from them.
_PINS = "".join(
	f'{name}="{value}"\n'
	for name, value in (
		("NGINX_VERSION", NGINX_VERSION),
		("NGINX_PKG_RELEASE", NGINX_PKG_RELEASE),
		("LUAJIT2_REF", LUAJIT2_REF),
		("LUA_NGINX_MODULE_VERSION", LUA_NGINX_MODULE_VERSION),
		("STREAM_LUA_MODULE_REF", STREAM_LUA_MODULE_REF),
		("NDK_VERSION", NDK_VERSION),
		("LUA_RESTY_CORE_VERSION", LUA_RESTY_CORE_VERSION),
		("LUA_RESTY_LRUCACHE_VERSION", LUA_RESTY_LRUCACHE_VERSION),
		("LUA_CJSON_VERSION", LUA_CJSON_VERSION),
		("HEADERS_MORE_VERSION", HEADERS_MORE_VERSION),
	)
)

# --- Stock nginx.org/Debian `nginx` package paths (build.sh §Paths). apt OWNS the
# binary/conf/log/pid; we only ADD under clearly-nginx-named dirs. SRC_DIR is gone — the
# committed assets now arrive as pyinfra file uploads, not from the script's own dir. ---
_PATHS = (
	'CONF_DIR="/etc/nginx"\n'
	'HTML_DIR="/usr/share/nginx/html"\n'
	'LUA_DIR="/etc/nginx/lua"\n'
	'MODULES_DIR="/etc/nginx/modules"\n'
	'SBIN_PATH="/usr/sbin/nginx"\n'
	'RUN_DIR="/run/nginx"\n'
	'LOG_DIR="/var/log/nginx"\n'
	'STATE_DIR="/var/lib/nginx"\n'
	'BUILD_DIR="/usr/local/src/nginx-build"\n'
	"export DEBIAN_FRONTEND=noninteractive\n"
)

# Where the ssl_preread patch is uploaded to (build.sh read it from $SRC_DIR/patches/).
_PATCH_REMOTE = "/usr/local/src/nginx-build/nginx-stream_ssl_preread_no_skip.patch"

# fetch <url> <out> — download once, reuse on re-run, retry a transient GitHub/codeload
# blip a few times, fail loud after 5. Verbatim from build.sh; prepended to every section
# that fetches. Kept as-is so a pyinfra `_retries` re-run reuses already-downloaded tarballs.
_FETCH = r"""fetch() {
	local url="$1" out="$2" attempt
	if [ -f "$out" ]; then
		echo "  reuse $out"
		return
	fi
	echo "  fetch $url"
	for attempt in 1 2 3 4 5; do
		if curl -fsSL --retry 3 --retry-all-errors --output "$out.part" "$url"; then
			mv "$out.part" "$out"
			return
		fi
		echo "  fetch attempt $attempt failed, retrying in $((attempt * 3))s ..." >&2
		rm -f "$out.part"
		sleep "$((attempt * 3))"
	done
	echo "FATAL: could not fetch $url after 5 attempts" >&2
	exit 1
}
"""


def _script(body: str, *, fetch: bool = False) -> str:
	"""A section's bash: `set -euo pipefail`, the pinned-version + paths preamble, the
	fetch() helper when needed, then the verbatim build.sh section body. Run under bash."""
	pre = "set -euo pipefail\n" + _PINS + _PATHS
	if fetch:
		pre += _FETCH
	return pre + body


# --- §1. Base nginx from the official nginx.org stable repo, PINNED to an exact version.
# One signed apt transaction installs the binary + OpenSSL and owns the stock paths.
# unhold→--reinstall forces the binary down fresh every run (ABI-bound to the modules we
# recompile). Fails loud if the repo serves something other than the pinned version. ---
_S1_BASE = r"""apt-get update
apt-get install -y --no-install-recommends ca-certificates curl gnupg lsb-release
install -d -m 0755 /usr/share/keyrings
curl -fsSL https://nginx.org/keys/nginx_signing.key \
	| gpg --batch --yes --dearmor -o /usr/share/keyrings/nginx-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/nginx-archive-keyring.gpg] https://nginx.org/packages/ubuntu $(lsb_release -cs) nginx" \
	> /etc/apt/sources.list.d/nginx.list
apt-get update
NGINX_PKG_VERSION="${NGINX_VERSION}-${NGINX_PKG_RELEASE}~$(lsb_release -cs)"
apt-mark unhold nginx 2>/dev/null || true
apt-get install -y --reinstall --no-install-recommends "nginx=${NGINX_PKG_VERSION}"
apt-mark hold nginx
INSTALLED_VERSION="$("$SBIN_PATH" -v 2>&1 | sed 's#.*nginx/##')"
if [ "$INSTALLED_VERSION" != "$NGINX_VERSION" ]; then
	echo "FATAL: pinned nginx ${NGINX_VERSION} but installed ${INSTALLED_VERSION}" >&2
	exit 1
fi
echo "installed stock nginx ${NGINX_VERSION} (${NGINX_PKG_VERSION}) from nginx.org"
"""

# --- §2. OpenResty luajit2 (the Lua module's REQUIRED fork; ships in no apt repo).
# Installs to /usr/local; the lua .so links against it via rpath (set in §4's configure). ---
_S2_LUAJIT = r"""mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"
fetch "https://github.com/openresty/luajit2/archive/refs/tags/${LUAJIT2_REF}.tar.gz" "luajit2.tar.gz"
rm -rf "luajit2-src"
mkdir luajit2-src
tar -xzf luajit2.tar.gz -C luajit2-src --strip-components=1
make -C luajit2-src -j"$(nproc)"
make -C luajit2-src install
ldconfig
"""

# --- §3. nginx source MATCHING the installed binary + the module sources (NDK before
# lua-nginx-module). We don't install this nginx — we build its modules (+ patched binary). ---
_S3_FETCH = r"""cd "$BUILD_DIR"
fetch "https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz" "nginx.tar.gz"
fetch "https://github.com/vision5/ngx_devel_kit/archive/refs/tags/v${NDK_VERSION}.tar.gz" "ndk.tar.gz"
fetch "https://github.com/openresty/lua-nginx-module/archive/refs/tags/v${LUA_NGINX_MODULE_VERSION}.tar.gz" "lua-nginx-module.tar.gz"
fetch "https://github.com/openresty/stream-lua-nginx-module/archive/refs/tags/${STREAM_LUA_MODULE_REF}.tar.gz" "stream-lua-nginx-module.tar.gz"
fetch "https://github.com/openresty/headers-more-nginx-module/archive/refs/tags/v${HEADERS_MORE_VERSION}.tar.gz" "headers-more.tar.gz"
for pair in "nginx.tar.gz:nginx" "ndk.tar.gz:ndk" \
	"lua-nginx-module.tar.gz:lua-nginx-module" \
	"stream-lua-nginx-module.tar.gz:stream-lua-nginx-module" "headers-more.tar.gz:headers-more"; do
	tarball="${pair%%:*}"
	dir="${pair##*:}"
	rm -rf "$dir"
	mkdir "$dir"
	tar -xzf "$tarball" -C "$dir" --strip-components=1
done
"""

# --- §3b + §4 + §4b. Patch nginx core's stream ssl_preread (so preread_by_lua sees the
# SNI), then ONE configure builds BOTH the patched nginx binary and the dynamic .so's
# (mirrors the apt build's flags + paths, +--with-compat +rpath). Install the modules,
# then REPLACE the apt binary with our same-version patched recompile, and assert the
# running binary is genuinely the recompile (its -V carries the --add-dynamic-module args
# the apt binary never had). The patch source is the ONE non-verbatim line: read from the
# uploaded $BUILD_DIR path instead of build.sh's $SRC_DIR/patches/. ---
_S4_COMPILE = r"""cd "$BUILD_DIR"
patch -p1 -d nginx < "$BUILD_DIR/nginx-stream_ssl_preread_no_skip.patch"
cd "$BUILD_DIR/nginx"
LUAJIT_LIB=/usr/local/lib LUAJIT_INC=/usr/local/include/luajit-2.1 \
./configure \
	--prefix=/etc/nginx \
	--sbin-path=/usr/sbin/nginx \
	--modules-path=/usr/lib/nginx/modules \
	--conf-path=/etc/nginx/nginx.conf \
	--error-log-path=/var/log/nginx/error.log \
	--http-log-path=/var/log/nginx/access.log \
	--pid-path=/run/nginx.pid \
	--lock-path=/run/nginx.lock \
	--http-client-body-temp-path=/var/cache/nginx/client_temp \
	--http-proxy-temp-path=/var/cache/nginx/proxy_temp \
	--http-fastcgi-temp-path=/var/cache/nginx/fastcgi_temp \
	--http-uwsgi-temp-path=/var/cache/nginx/uwsgi_temp \
	--http-scgi-temp-path=/var/cache/nginx/scgi_temp \
	--user=nginx \
	--group=nginx \
	--with-compat \
	--with-file-aio \
	--with-threads \
	--with-http_addition_module \
	--with-http_auth_request_module \
	--with-http_dav_module \
	--with-http_flv_module \
	--with-http_gunzip_module \
	--with-http_gzip_static_module \
	--with-http_mp4_module \
	--with-http_random_index_module \
	--with-http_realip_module \
	--with-http_secure_link_module \
	--with-http_slice_module \
	--with-http_ssl_module \
	--with-http_stub_status_module \
	--with-http_sub_module \
	--with-http_v2_module \
	--with-stream \
	--with-stream_realip_module \
	--with-stream_ssl_module \
	--with-stream_ssl_preread_module \
	--with-ld-opt="-Wl,-rpath,/usr/local/lib" \
	--add-dynamic-module="$BUILD_DIR/ndk" \
	--add-dynamic-module="$BUILD_DIR/lua-nginx-module" \
	--add-dynamic-module="$BUILD_DIR/stream-lua-nginx-module" \
	--add-dynamic-module="$BUILD_DIR/headers-more"
make -j"$(nproc)"
install -d "$MODULES_DIR"
install -m 0644 objs/*.so "$MODULES_DIR/"
install -m 0755 objs/nginx "$SBIN_PATH.atlas-patched"
mv -f "$SBIN_PATH.atlas-patched" "$SBIN_PATH"
"$SBIN_PATH" -V 2>&1 | grep -q -- "--add-dynamic-module=.*stream-lua" \
	|| { echo "FATAL: patched nginx not the recompile — wrong binary installed" >&2; exit 1; }
"""

# --- §5 + §5b. Pure-Lua resty libs (lua-resty-core is MANDATORY — nginx won't start
# without it) + the lua-cjson C module (NOT bundled with vanilla nginx; built against
# luajit2's headers). Loaded at runtime from /usr/local/{share,lib}/lua/5.1. ---
_S5_LUA = r"""cd "$BUILD_DIR"
fetch "https://github.com/openresty/lua-resty-core/archive/refs/tags/v${LUA_RESTY_CORE_VERSION}.tar.gz" "lua-resty-core.tar.gz"
fetch "https://github.com/openresty/lua-resty-lrucache/archive/refs/tags/v${LUA_RESTY_LRUCACHE_VERSION}.tar.gz" "lua-resty-lrucache.tar.gz"
for pair in "lua-resty-core.tar.gz:lua-resty-core" "lua-resty-lrucache.tar.gz:lua-resty-lrucache"; do
	tarball="${pair%%:*}"
	dir="${pair##*:}"
	rm -rf "$dir"
	mkdir "$dir"
	tar -xzf "$tarball" -C "$dir" --strip-components=1
	make -C "$dir" install LUA_LIB_DIR=/usr/local/share/lua/5.1
done
fetch "https://github.com/openresty/lua-cjson/archive/refs/tags/${LUA_CJSON_VERSION}.tar.gz" "lua-cjson.tar.gz"
rm -rf "lua-cjson"
mkdir "lua-cjson"
tar -xzf "lua-cjson.tar.gz" -C "lua-cjson" --strip-components=1
make -C "lua-cjson" LUA_INCLUDE_DIR=/usr/local/include/luajit-2.1
make -C "lua-cjson" install
ldconfig
"""

# --- §7. Runtime dirs + cert layout under the stock nginx state/run dirs. certs/ stays
# root-only (0750/0640 key — the SSL core reads it in the MASTER, never a worker); $STATE_DIR
# itself is root:nginx 0770 so a worker can rename its live map snapshot in. The placeholder
# self-signed cert is regenerated every bake so the `-subj` copy below actually takes effect.
# KEEP THE -subj BYTE-IDENTICAL to atlas.proxy.PLACEHOLDER_CERT_SUBJECT. ---
_S7_RUNTIME = r"""install -d -m 0750 "$RUN_DIR"
install -d -m 0750 "$STATE_DIR/certs"
install -d -o root -g nginx -m 0770 "$STATE_DIR"
install -d -o root -g nginx -m 0750 "$STATE_DIR/acme"
: > "$STATE_DIR/region"
install -d -m 0750 "$STATE_DIR/certs/_placeholder"
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
	-keyout "$STATE_DIR/certs/_placeholder/privkey.pem" \
	-out "$STATE_DIR/certs/_placeholder/fullchain.pem" \
	-subj "/CN=This domain is not connected to a site yet/O=Frappe Cloud/OU=Connect it in your dashboard: frappe.dev\/domains"
chmod 0640 "$STATE_DIR/certs/_placeholder/privkey.pem"
ln -sfn _placeholder/fullchain.pem "$STATE_DIR/certs/fullchain.pem"
ln -sfn _placeholder/privkey.pem   "$STATE_DIR/certs/privkey.pem"
"""

# --- §8. systemd: enable nginx (the drop-in override was uploaded above). On a live
# systemd, daemon-reload + enable; on a chroot/container build (no /run/systemd/system),
# symlink the package unit into multi-user.target.wants so a real boot starts it. ---
_S8_ENABLE = r"""if [ -d /run/systemd/system ]; then
	systemctl daemon-reload
	systemctl enable nginx.service
else
	install -d /etc/systemd/system/multi-user.target.wants
	ln -sf /lib/systemd/system/nginx.service \
		/etc/systemd/system/multi-user.target.wants/nginx.service
fi
"""

# --- §9. Validate: `nginx -t` under a raised ulimit (the stream{} 10000-19999 pool needs
# ~20000 fds), which also proves the load_module lines resolve the .so's and cjson +
# lua-resty-core load at init; the recompile assert proves the patched binary is running;
# is-enabled proves it boots. ---
_VERIFY = r"""set -euo pipefail
ulimit -n 1048576 2>/dev/null || ulimit -n $(ulimit -Hn)
nginx -t
nginx -V 2>&1 | grep -q -- '--add-dynamic-module=.*stream-lua'
systemctl is-enabled nginx
"""


@deploy("build")
def build():
	# §1 — base nginx from nginx.org, pinned + held; fail loud on a version mismatch.
	server.shell(
		name="install pinned nginx base from nginx.org (§1)",
		commands=[_script(_S1_BASE)],
		_shell_executable="bash",
		_timeout=600,
		_retries=2,
		_retry_delay=15,
	)
	# §1 (toolchain) — luajit2 + dynamic-module compiler deps. --no-install-recommends to
	# match build.sh; PCRE2/zlib/OpenSSL -dev headers must match the apt nginx's. python3
	# is the stdlib-only stream-admin client's interpreter.
	apt.packages(
		name="install build toolchain + python3 (§1)",
		packages=["build-essential", "libpcre2-dev", "zlib1g-dev", "libssl-dev", "python3"],
		update=True,
		no_recommends=True,
		_retries=3,
		_retry_delay=10,
	)
	# §2 — OpenResty luajit2.
	server.shell(
		name="compile + install OpenResty luajit2 (§2)",
		commands=[_script(_S2_LUAJIT, fetch=True)],
		_shell_executable="bash",
		_timeout=600,
		_retries=3,
		_retry_delay=15,
	)
	# §3 — fetch the nginx source + the 4 module sources, extract them.
	server.shell(
		name="fetch + extract nginx source + module sources (§3)",
		commands=[_script(_S3_FETCH, fetch=True)],
		_shell_executable="bash",
		_timeout=600,
		_retries=3,
		_retry_delay=15,
	)
	# §3b — upload the committed ssl_preread patch to the build dir (build.sh applied it
	# from its own $SRC_DIR/patches/; here it arrives as a file upload).
	files.put(
		name="upload ssl_preread patch to build dir (§3b)",
		src=os.path.join(_FILES, "patches", "nginx-stream_ssl_preread_no_skip.patch"),
		dest=_PATCH_REMOTE,
		create_remote_dir=True,
		add_deploy_dir=False,
	)
	# §3b/§4/§4b — patch + configure + make the patched nginx binary and the dynamic .so's,
	# install the modules, replace the apt binary, assert the recompile is running. The compile.
	server.shell(
		name="patch + build nginx binary + dynamic modules, replace apt binary (§4)",
		commands=[_script(_S4_COMPILE)],
		_shell_executable="bash",
		_timeout=1800,
	)
	# §5/§5b — the pure-Lua resty libs + the lua-cjson C module.
	server.shell(
		name="build lua-resty-core/lrucache + lua-cjson (§5)",
		commands=[_script(_S5_LUA, fetch=True)],
		_shell_executable="bash",
		_timeout=600,
		_retries=3,
		_retry_delay=15,
	)
	# §6 — install the committed stack at stock nginx paths. OVERWRITE the package's own
	# /etc/nginx/nginx.conf with our single-file config (it carries the load_module lines).
	files.put(
		name="install nginx.conf (§6)",
		src=os.path.join(_FILES, "conf", "nginx.conf"),
		dest="/etc/nginx/nginx.conf",
		mode="644",
		add_deploy_dir=False,
	)
	# The Lua module set (http + stream tries, acme + sni forks, unconfigured terminator).
	files.sync(
		name="install lua modules to /etc/nginx/lua (§6)",
		src=os.path.join(_FILES, "lua"),
		dest="/etc/nginx/lua",
		mode="644",
		add_deploy_dir=False,
	)
	files.put(
		name="install not_found.html (§6)",
		src=os.path.join(_FILES, "html", "not_found.html"),
		dest="/usr/share/nginx/html/not_found.html",
		mode="644",
		add_deploy_dir=False,
	)
	files.put(
		name="install domain_unconfigured.html (§6)",
		src=os.path.join(_FILES, "html", "domain_unconfigured.html"),
		dest="/usr/share/nginx/html/domain_unconfigured.html",
		mode="644",
		add_deploy_dir=False,
	)
	# The stdlib-only stream-admin line-protocol client, on PATH for the controller.
	files.put(
		name="install stream-admin client to /usr/local/bin (§6)",
		src=os.path.join(_FILES, "guest", "stream-admin"),
		dest="/usr/local/bin/stream-admin",
		mode="755",
		add_deploy_dir=False,
	)
	# §7 — runtime dirs + the placeholder self-signed cert.
	server.shell(
		name="runtime dirs + placeholder self-signed cert (§7)",
		commands=[_script(_S7_RUNTIME)],
		_shell_executable="bash",
		_timeout=120,
	)
	# §8 — the thin systemd drop-in over the package's own nginx.service.
	files.put(
		name="install nginx.service.d/atlas.conf drop-in (§8)",
		src=os.path.join(_FILES, "guest", "nginx.service.d", "atlas.conf"),
		dest="/etc/systemd/system/nginx.service.d/atlas.conf",
		mode="644",
		create_remote_dir=True,
		add_deploy_dir=False,
	)
	# §8 — enable nginx (start on boot; Atlas's finalize starts it in the golden).
	server.shell(
		name="systemd daemon-reload + enable nginx (§8)",
		commands=[_script(_S8_ENABLE)],
		_shell_executable="bash",
		_timeout=120,
	)


@deploy("verify")
def verify():
	# §9 — `nginx -t` under a raised ulimit (proves the config + every dynamic module +
	# cjson/lua-resty-core load), the recompile assert (proves the patched binary is the one
	# running), and is-enabled (proves it boots).
	server.shell(
		name="nginx -t (raised ulimit) + patched-recompile assert + service enabled (§9)",
		commands=[_VERIFY],
		_shell_executable="bash",
	)
