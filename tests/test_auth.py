"""Auth exemptions — the SSE log stream must be readable without a bearer token, because
the browser's EventSource cannot send an Authorization header."""

from __future__ import annotations

from chef.app.auth import is_public_path


def test_public_discovery_paths():
    for path in ("/", "/healthz", "/llms.txt", "/openapi.json", "/docs"):
        assert is_public_path(path), path


def test_sse_log_stream_is_public():
    # EventSource cannot authenticate, so the log stream is open.
    assert is_public_path("/bakes/54560eba-fdfe-4640-88ff-5b329f25209f/logs")
    assert is_public_path("/bakes/any-id/logs")


def test_protected_paths_are_not_public():
    for path in ("/recipes/", "/bakes/abc", "/bakes/abc/abort", "/images/"):
        assert not is_public_path(path), path
