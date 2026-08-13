"""Release resolver (``chef.releases``) + the ``TrackedRelease`` store.

The resolver is exercised with a stubbed ``subprocess.run`` so no network/git is needed;
the store round-trips against a temp SQLite DB.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from chef import releases, store

# --- resolver (git ls-remote stubbed) ----------------------------------------


class _FakeProc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


@pytest.fixture(autouse=True)
def _clear_refs_cache():
    releases._refs_cache.clear()
    yield
    releases._refs_cache.clear()


def _stub_git(monkeypatch, stdout="", returncode=0, stderr=""):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return _FakeProc(stdout=stdout, returncode=returncode, stderr=stderr)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_repo_url_forms():
    assert releases.repo_url("frappe/pilot") == "https://github.com/frappe/pilot"
    assert releases.repo_url("https://example.com/x.git") == "https://example.com/x.git"
    assert releases.repo_url("git@github.com:frappe/pilot.git") == "git@github.com:frappe/pilot.git"


def test_resolve_ref_tag(monkeypatch):
    _stub_git(monkeypatch, stdout="deadbeef00000000000000000000000000000000\trefs/tags/v1.2.3\n")
    assert releases.resolve_ref("frappe/pilot", "v1.2.3") == "deadbeef00000000000000000000000000000000"


def test_resolve_ref_prefers_peeled_annotated_commit(monkeypatch):
    # An annotated tag yields both the tag object and the peeled ^{} commit; want the commit.
    out = (
        "1111111111111111111111111111111111111111\trefs/tags/v1\n"
        "2222222222222222222222222222222222222222\trefs/tags/v1^{}\n"
    )
    _stub_git(monkeypatch, stdout=out)
    assert releases.resolve_ref("frappe/pilot", "v1") == "2222222222222222222222222222222222222222"


def test_resolve_ref_branch(monkeypatch):
    _stub_git(monkeypatch, stdout="abc1230000000000000000000000000000000000\trefs/heads/develop\n")
    assert releases.resolve_ref("frappe/pilot", "develop") == "abc1230000000000000000000000000000000000"


def test_resolve_ref_sha_passthrough(monkeypatch):
    calls = _stub_git(monkeypatch, stdout="")  # a full SHA is trusted without calling git
    sha = "a" * 40
    assert releases.resolve_ref("frappe/pilot", sha) == sha
    assert calls == []  # never shelled out


def test_resolve_ref_not_found(monkeypatch):
    _stub_git(monkeypatch, stdout="")  # empty ls-remote → ref does not exist
    assert releases.resolve_ref("frappe/pilot", "v9.9.9") is None


def test_resolve_ref_git_error_raises(monkeypatch):
    _stub_git(monkeypatch, returncode=128, stderr="fatal: repository not found")
    with pytest.raises(releases.ReleaseError):
        releases.resolve_ref("frappe/nope", "v1")


def test_list_refs_sorts_newest_first_and_strips_prefix(monkeypatch):
    out = (
        "aaa\trefs/tags/v0.0.2-pre-alpha\n"
        "bbb\trefs/tags/v0.0.10-pre-alpha\n"
        "ccc\trefs/tags/v0.0.9-pre-alpha\n"
    )
    _stub_git(monkeypatch, stdout=out)
    tags = releases.list_refs("frappe/pilot")
    # numeric-aware sort: 10 > 9 > 2, and the refs/tags/ prefix is stripped
    assert tags == ["v0.0.10-pre-alpha", "v0.0.9-pre-alpha", "v0.0.2-pre-alpha"]


def test_list_refs_cached(monkeypatch):
    calls = _stub_git(monkeypatch, stdout="aaa\trefs/tags/v1\n")
    releases.list_refs("frappe/pilot")
    releases.list_refs("frappe/pilot")  # served from cache
    assert len(calls) == 1


# --- store (TrackedRelease) ---------------------------------------------------


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CHEF_DATABASE_URL", f"sqlite:///{tmp_path / 'chef.db'}")
    from chef.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(store, "_engine", None)
    store.init_db()
    yield
    get_settings.cache_clear()


def test_pin_crud(temp_db):
    assert store.get_pin("frappe/pilot") is None
    assert store.list_pins() == []

    store.set_pin("frappe/pilot", "v0.0.23-pre-alpha", "deadbeef")
    pin = store.get_pin("frappe/pilot")
    assert pin is not None
    assert pin.ref == "v0.0.23-pre-alpha"
    assert pin.sha == "deadbeef"

    # update in place (same PK)
    store.set_pin("frappe/pilot", "v0.0.22-pre-alpha", "cafef00d")
    assert store.get_pin("frappe/pilot").ref == "v0.0.22-pre-alpha"
    assert len(store.list_pins()) == 1

    assert store.delete_pin("frappe/pilot") is True
    assert store.get_pin("frappe/pilot") is None
    assert store.delete_pin("frappe/pilot") is False  # already gone
