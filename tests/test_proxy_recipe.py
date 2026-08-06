"""The native proxy recipe (a pyinfra port of Atlas's proxy build.sh — no shim).

The recipe compiles the nginx + OpenResty Lua stack from committed assets under
``recipes/proxy/files/`` (there is no ``tree/`` / ``build.sh`` any more). These tests load
the recipe, assert the manifest parses and the inputs/modes/phases/publish/size are wired
as intended, that both phases resolve to callables (and there is no warm_arm), and that the
committed assets actually landed under ``files/``. They do NOT bake (no fleet).
"""

from __future__ import annotations

from pathlib import Path

from chef.engine.recipe import load_recipe

RECIPES_DIR = Path(__file__).resolve().parent.parent / "recipes"

# The Lua module set build.sh installs at /etc/nginx/lua (the http + stream tries, the
# acme + sni forks, and the unconfigured-domain terminator).
_LUA_FILES = (
	"acme_persist.lua",
	"acme_router.lua",
	"admin.lua",
	"persist.lua",
	"router.lua",
	"sni_passthrough.lua",
	"sni_persist.lua",
	"sni_router.lua",
	"stream_admin.lua",
	"stream_persist.lua",
	"stream_router.lua",
	"unconfigured.lua",
)


def test_proxy_manifest_and_phases():
	recipe = load_recipe(RECIPES_DIR, "proxy")
	m = recipe.manifest

	assert m.name == "proxy"
	assert m.version == "1.0.0"
	assert m.base_image == "ubuntu-24.04"
	assert m.modes == ["cold"]
	assert [p["type"] for p in m.publish] == ["atlas-base-image", "local"]
	assert m.publish[0]["name"] == "proxy-chef"

	# proxy is cold-only: build + verify resolve to callables, no warm_arm.
	assert callable(recipe.load_phase("build"))
	assert callable(recipe.load_phase("verify"))
	assert recipe.has_phase("warm_arm") is False
	assert recipe.load_phase("warm_arm") is None


def test_proxy_size():
	m = load_recipe(RECIPES_DIR, "proxy").manifest
	assert m.size.vcpus == 2
	assert m.size.memory_megabytes == 1024
	assert m.size.disk_gigabytes == 20


def test_proxy_is_native_not_shim():
	"""The shim's ``tree/`` (build.sh + the uploaded source tree) is gone; the assets now
	live under ``files/``."""
	proxy = RECIPES_DIR / "proxy"
	assert not (proxy / "tree").exists(), "recipes/proxy/tree/ should be removed"
	assert (proxy / "files").is_dir()


def test_proxy_files_assets_present():
	files = RECIPES_DIR / "proxy" / "files"

	# The single-file nginx.conf (carries the load_module lines for the dynamic modules).
	assert (files / "conf" / "nginx.conf").is_file()

	# The ssl_preread core patch applied to the nginx source before configure.
	assert (files / "patches" / "nginx-stream_ssl_preread_no_skip.patch").is_file()

	# The stdlib-only stream-admin line-protocol client installed to /usr/local/bin.
	assert (files / "guest" / "stream-admin").is_file()

	# The thin systemd drop-in over the package's own nginx.service.
	assert (files / "guest" / "nginx.service.d" / "atlas.conf").is_file()

	# The two local HTML pages served by the proxy.
	assert (files / "html" / "not_found.html").is_file()
	assert (files / "html" / "domain_unconfigured.html").is_file()

	# The full Lua module set, and nothing under lua/ but these .lua files.
	lua_dir = files / "lua"
	for name in _LUA_FILES:
		assert (lua_dir / name).is_file(), f"missing lua module: {name}"
	assert sorted(p.name for p in lua_dir.glob("*.lua")) == sorted(_LUA_FILES)
