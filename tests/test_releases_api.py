"""HTTP tests for the ``/releases`` router + release fields on recipes/bakes.

A per-test client points at a temp SQLite DB (so pins don't leak) and reads the real
recipes on disk. ``resolve_ref`` / ``list_refs`` are monkeypatched in the router so no git
or network is needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import chef.app.routers.recipes as recipes_router
import chef.app.routers.releases as releases_router
from chef import store

AUTH = {"Authorization": "Bearer chef-dev-token"}


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CHEF_DATABASE_URL", f"sqlite:///{tmp_path / 'chef.db'}")
    from chef.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(store, "_engine", None)

    from chef.app.main import app

    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def test_list_pins_empty(client):
    r = client.get("/releases/", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == []


def test_set_pin_validates_and_persists(client, monkeypatch):
    monkeypatch.setattr(releases_router, "resolve_ref", lambda repo, ref: "deadbeefcafe")

    r = client.put("/releases/", headers=AUTH, json={"repo": "frappe/pilot", "ref": "v0.0.23-pre-alpha"})
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "repo": "frappe/pilot",
        "ref": "v0.0.23-pre-alpha",
        "sha": "deadbeefcafe",
        "resolved_at": body["resolved_at"],
    }

    listed = client.get("/releases/", headers=AUTH).json()
    assert [p["repo"] for p in listed] == ["frappe/pilot"]


def test_set_pin_unknown_ref_422(client, monkeypatch):
    monkeypatch.setattr(releases_router, "resolve_ref", lambda repo, ref: None)
    r = client.put("/releases/", headers=AUTH, json={"repo": "frappe/pilot", "ref": "nope"})
    assert r.status_code == 422
    assert "not found" in r.json()["detail"]


def test_delete_pin(client, monkeypatch):
    monkeypatch.setattr(releases_router, "resolve_ref", lambda repo, ref: "sha")
    client.put("/releases/", headers=AUTH, json={"repo": "frappe/pilot", "ref": "v1"})

    r = client.delete("/releases/frappe/pilot", headers=AUTH)
    assert r.status_code == 204
    assert client.get("/releases/", headers=AUTH).json() == []

    # deleting a missing pin → 404
    assert client.delete("/releases/frappe/pilot", headers=AUTH).status_code == 404


def test_refs_picker(client, monkeypatch):
    monkeypatch.setattr(releases_router, "list_refs", lambda repo: ["v2", "v1"])
    r = client.get("/releases/refs", headers=AUTH, params={"repo": "frappe/pilot"})
    assert r.status_code == 200
    assert r.json() == {"repo": "frappe/pilot", "refs": ["v2", "v1"]}


def test_recipe_detail_exposes_tracked(client, monkeypatch):
    # pilot tracks frappe/pilot; unpinned → ref/sha null
    r = client.get("/recipes/pilot", headers=AUTH)
    assert r.status_code == 200
    tracked = {t["repo"]: t for t in r.json()["tracked"]}
    assert "frappe/pilot" in tracked
    assert tracked["frappe/pilot"]["ref"] is None

    # once pinned, the detail reflects it
    monkeypatch.setattr(releases_router, "resolve_ref", lambda repo, ref: "abc123")
    client.put("/releases/", headers=AUTH, json={"repo": "frappe/pilot", "ref": "v0.0.23-pre-alpha"})
    tracked = {t["repo"]: t for t in client.get("/recipes/pilot", headers=AUTH).json()["tracked"]}
    assert tracked["frappe/pilot"]["ref"] == "v0.0.23-pre-alpha"
    assert tracked["frappe/pilot"]["sha"] == "abc123"


def test_bake_accepts_releases_override(client, monkeypatch):
    # enqueue is best-effort; stub it so we exercise only row creation
    async def _no_enqueue(bake_id, settings):
        return True

    import chef.app.routers.recipes as recipes_router

    monkeypatch.setattr(recipes_router, "_enqueue_bake", _no_enqueue)

    r = client.post(
        "/recipes/pilot/bake",
        headers=AUTH,
        json={"inputs": {}, "mode": "cold", "releases": {"frappe/pilot": "v0.0.20-pre-alpha"}},
    )
    assert r.status_code == 202
    bake_id = r.json()["bake_id"]
    bake = store.get_bake(bake_id)
    assert bake.releases == {"frappe/pilot": "v0.0.20-pre-alpha"}


# --- validation also checks the release --------------------------------------


def test_validate_reports_unpinned_release(client):
    r = client.post("/recipes/validate", headers=AUTH, json={"name": "pilot", "inputs": {}})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert any("frappe/pilot" in e["message"] for e in body["errors"])


def test_validate_ok_when_pinned(client, monkeypatch):
    monkeypatch.setattr(releases_router, "resolve_ref", lambda repo, ref: "sha")
    monkeypatch.setattr(recipes_router, "resolve_ref", lambda repo, ref: "sha")
    client.put("/releases/", headers=AUTH, json={"repo": "frappe/pilot", "ref": "v0.0.23-pre-alpha"})
    r = client.post("/recipes/validate", headers=AUTH, json={"name": "pilot", "inputs": {}})
    assert r.json()["ok"] is True


def test_validate_reports_bad_override_ref(client, monkeypatch):
    monkeypatch.setattr(recipes_router, "resolve_ref", lambda repo, ref: None)  # not found
    r = client.post(
        "/recipes/validate",
        headers=AUTH,
        json={"name": "pilot", "inputs": {}, "releases": {"frappe/pilot": "v9.9.9-nope"}},
    )
    body = r.json()
    assert body["ok"] is False
    assert any("not found" in e["message"] for e in body["errors"])


def test_bake_creation_422_when_unpinned(client):
    r = client.post("/recipes/pilot/bake", headers=AUTH, json={"inputs": {}, "mode": "cold"})
    assert r.status_code == 422
    assert "frappe/pilot" in r.json()["detail"]
