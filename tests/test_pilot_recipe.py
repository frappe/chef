"""The `pilot` golden recipe: a working Pilot admin console + a Frappe site."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chef.config import get_settings
from chef.engine.recipe import load_recipe


def _pilot():
    return load_recipe(get_settings().recipes_dir, "pilot")


def test_pilot_manifest():
    m = _pilot().manifest
    assert m.name == "pilot"
    assert m.base_image == "ubuntu-24.04"
    assert m.modes == ["cold", "warm", "both"]
    # boots fat (yarn asset build) then a clone serves at the restore size
    assert m.size.build_memory_megabytes == 6144
    assert m.size.effective_build_memory_megabytes == 6144
    # server golden (base image), signup golden (bench snapshot), then the local dev sink
    assert [p["type"] for p in m.publish] == ["atlas-base-image", "atlas-bench-snapshot", "local"]
    assert m.publish[0]["name"] == "pilot-chef"
    assert m.publish[0]["register_user_image"] is True


def test_pilot_inputs_and_phases():
    r = _pilot()
    schema = r.input_schema()
    assert set(schema["properties"]) == {
        "admin_password", "site_name", "admin_domain", "frappe_branch"
    }
    resolved = r.validate_inputs({})
    assert resolved["site_name"] == "site.local"
    assert resolved["admin_domain"] == "admin.local"
    assert resolved["frappe_branch"] == "version-16"
    assert callable(r.load_phase("build"))
    assert callable(r.load_phase("verify"))
    # warm_arm primes the site before a warm capture (both/warm bakes)
    assert callable(r.load_phase("warm_arm"))
