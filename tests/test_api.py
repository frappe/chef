"""API smoke tests via FastAPI's ``TestClient``.

Deliberately independent of Redis: the bake endpoint's enqueue is monkeypatched, so the
row-creation + 202 path is exercised without a live broker. Recipes (``hello``/``nginx``)
are read from disk.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from chef import store
from chef.app.main import app

AUTH = {"Authorization": "Bearer chef-dev-token"}


@pytest.fixture(scope="module")
def client():
    store.init_db()
    with TestClient(app) as c:
        yield c


# --- public surface ----------------------------------------------------------


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_llms_txt_is_public_and_lists_endpoints(client):
    r = client.get("/llms.txt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "/recipes/" in r.text
    assert "/bakes/{bake_id}/logs" in r.text


def test_openapi_available(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert "/recipes/{name}/bake" in r.json()["paths"]


# --- recipes -----------------------------------------------------------------


def test_list_recipes_includes_hello_and_nginx(client):
    r = client.get("/recipes/", headers=AUTH)
    assert r.status_code == 200
    names = {item["name"] for item in r.json()}
    assert {"hello", "nginx"} <= names


def test_recipe_detail_exposes_input_schema(client):
    r = client.get("/recipes/nginx", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert "worker_processes" in body["input_schema"]["properties"]
    assert body["input_schema"]["properties"]["worker_processes"]["type"] == "string"


def test_recipe_template(client):
    r = client.get("/recipes/template", headers=AUTH)
    assert r.status_code == 200
    assert "recipe.toml" in r.json()["files"]


def test_unknown_recipe_404(client):
    r = client.get("/recipes/does-not-exist", headers=AUTH)
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


def test_list_exposes_compose(client):
    r = client.get("/recipes/", headers=AUTH)
    by_name = {item["name"]: item for item in r.json()}
    assert by_name["webapp"]["compose"] == ["hello", "nginx"]
    assert by_name["hello"]["compose"] == []  # a plain recipe composes nothing


def test_detail_exposes_lineage_and_phase_sources(client):
    r = client.get("/recipes/webapp", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["compose"] == ["hello", "nginx"]
    assert body["lineage"] == ["hello", "nginx", "webapp"]
    assert body["phase_sources"]["build"] == ["hello", "nginx"]
    assert body["phase_sources"]["verify"] == ["nginx"]
    # the composed source view carries every stacked recipe's files, keyed by recipe.
    assert "hello/recipe.py" in body["source"]
    assert "nginx/recipe.toml" in body["source"]


def test_validate_ok_for_good_inputs(client):
    r = client.post(
        "/recipes/validate",
        json={"name": "nginx", "inputs": {"worker_processes": "auto"}},
        headers=AUTH,
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_validate_reports_bad_worker_processes_type(client):
    r = client.post(
        "/recipes/validate",
        json={"name": "nginx", "inputs": {"worker_processes": 123}},
        headers=AUTH,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["errors"]


# --- auth --------------------------------------------------------------------


def test_protected_route_requires_token(client):
    assert client.get("/recipes/").status_code == 401
    assert client.get("/recipes/", headers=AUTH).status_code == 200


# --- bake (no redis) ---------------------------------------------------------


def test_bake_degrades_without_redis(client, monkeypatch):
    async def _no_enqueue(bake_id, settings):  # redis-down path
        return False

    monkeypatch.setattr("chef.app.routers.recipes._enqueue_bake", _no_enqueue)

    r = client.post(
        "/recipes/hello/bake",
        json={"inputs": {}, "mode": "cold"},
        headers=AUTH,
    )
    assert r.status_code == 202
    body = r.json()
    assert body["bake_id"]
    assert body["status"] == "queued"
    assert body["links"]["logs"].endswith("/logs")

    # The row is durable even though the enqueue was skipped.
    got = client.get(body["links"]["status"], headers=AUTH)
    assert got.status_code == 200
    assert got.json()["recipe"] == "hello"
