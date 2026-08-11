"""POST /images/{id}/propagate — the fleet fan-out trigger, via TestClient.

No network: the Atlas client's ``from_settings`` is monkeypatched to a fake that records
the ``distribute_image`` call and returns a handle, so the endpoint's resolve → trigger →
handle path runs without a live Atlas.
"""

from __future__ import annotations

import uuid

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


def _seed_image(location_type: str, location_uri: str = "pilot-chef") -> str:
    image_id = uuid.uuid4().hex
    store.create_image(
        store.ImageRecord(
            id=image_id,
            bake_id="bake-x",
            recipe="pilot",
            kind="cold",
            location_type=location_type,
            location_uri=location_uri,
        )
    )
    return image_id


class _FakeClient:
    def __init__(self):
        self.call = None

    def distribute_image(self, image, servers=None):
        self.call = {"image": image, "servers": servers}
        return {"image": image, "source": "host-1", "servers": ["host-2", "host-3"]}


def test_propagate_unknown_image_404(client):
    r = client.post(f"/images/{uuid.uuid4().hex}/propagate", headers=AUTH, json={})
    assert r.status_code == 404


def test_propagate_rejects_non_atlas_image(client):
    image_id = _seed_image("local")
    r = client.post(f"/images/{image_id}/propagate", headers=AUTH, json={})
    assert r.status_code == 409
    assert "atlas-base-image" in r.json()["detail"]


def test_propagate_triggers_atlas_distribute(client, monkeypatch):
    image_id = _seed_image("atlas-base-image", "pilot-chef")
    fake = _FakeClient()
    monkeypatch.setattr(
        "chef.atlas_client.AtlasClient.from_settings",
        classmethod(lambda cls, settings=None: fake),
    )

    r = client.post(f"/images/{image_id}/propagate", headers=AUTH, json={})

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["image"] == "pilot-chef"
    assert body["source"] == "host-1"
    assert body["servers"] == ["host-2", "host-3"]
    assert fake.call == {"image": "pilot-chef", "servers": None}


def test_propagate_passes_explicit_servers(client, monkeypatch):
    image_id = _seed_image("atlas-base-image", "pilot-chef")
    fake = _FakeClient()
    monkeypatch.setattr(
        "chef.atlas_client.AtlasClient.from_settings",
        classmethod(lambda cls, settings=None: fake),
    )

    r = client.post(f"/images/{image_id}/propagate", headers=AUTH, json={"servers": ["host-2"]})

    assert r.status_code == 200
    assert fake.call == {"image": "pilot-chef", "servers": ["host-2"]}
