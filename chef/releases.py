"""Release resolution for tracked repos — pure ``git`` calls, no GitHub API.

The release-tracking store (``chef.store.TrackedRelease``) pins a *ref* per repo; this
module turns a ref into the commit it points at and lists a repo's tags for the picker.
Everything goes through ``git ls-remote`` so there is no token, no rate limit, and no new
HTTP client — it works for public repos anonymously and resolves tags, branches and SHAs
uniformly.

  * :func:`resolve_ref` — ``ref`` → the 40-hex commit SHA (or ``None`` if it doesn't exist).
    Used to validate a pin when it's set and to re-record the SHA at bake time.
  * :func:`list_refs` — a repo's tags, newest-first, for the UI/CLI picker. Cached briefly
    since a fast-moving repo (pilot cuts several tags a day) shouldn't be re-hit per keystroke.
"""

from __future__ import annotations

import logging
import re
import subprocess
import time

logger = logging.getLogger("chef.releases")

_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_LS_REMOTE_TIMEOUT = 20
_REFS_CACHE_TTL = 60.0  # seconds; the tag list is polled by the picker, not authoritative

# repo -> (monotonic_deadline, refs)
_refs_cache: dict[str, tuple[float, list[str]]] = {}


class ReleaseError(RuntimeError):
    """A ``git`` invocation for release resolution failed (bad repo, network, timeout)."""


def repo_url(repo: str) -> str:
    """Normalise a repo identifier to a clone URL. Accepts ``owner/name`` (→ GitHub) or a
    full URL / ``git@`` remote (returned unchanged)."""
    repo = repo.strip()
    if repo.startswith(("http://", "https://", "git@", "ssh://")):
        return repo
    return f"https://github.com/{repo}"


def _git(*args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=_LS_REMOTE_TIMEOUT,
            check=False,  # we inspect returncode and raise ReleaseError ourselves
        )
    except FileNotFoundError as exc:  # git missing from the image
        raise ReleaseError("git is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise ReleaseError(f"git ls-remote timed out after {_LS_REMOTE_TIMEOUT}s") from exc
    if proc.returncode != 0:
        raise ReleaseError((proc.stderr or proc.stdout).strip() or "git ls-remote failed")
    return proc.stdout


def _parse_ls_remote(text: str) -> dict[str, str]:
    """``<sha>\\t<refname>`` lines → ``{refname: sha}``."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        sha, _, name = line.partition("\t")
        sha, name = sha.strip(), name.strip()
        if sha and name:
            out[name] = sha
    return out


def resolve_ref(repo: str, ref: str) -> str | None:
    """Resolve ``ref`` (a tag, branch or commit SHA) in ``repo`` to a commit SHA.

    Returns the SHA, or ``None`` when the ref does not exist. A full/abbrev SHA is trusted
    as-is (``ls-remote`` only lists refs, not arbitrary commits). For an *annotated* tag the
    peeled ``^{}`` commit is preferred over the tag object so the recorded SHA is the commit.
    """
    ref = ref.strip()
    if not ref:
        return None
    if _SHA_RE.match(ref):
        return ref.lower()

    refs = _parse_ls_remote(
        _git("ls-remote", repo_url(repo), ref, f"refs/tags/{ref}", f"refs/heads/{ref}")
    )
    if not refs:
        return None
    for name in (f"refs/tags/{ref}^{{}}", f"refs/tags/{ref}", f"refs/heads/{ref}", ref):
        if name in refs:
            return refs[name]
    return next(iter(refs.values()))


def _version_key(tag: str) -> tuple:
    """Best-effort version sort key: the tag's integer groups, then the raw string.

    Handles ``v0.0.23-pre-alpha`` (→ ``(0, 0, 23)``) without assuming strict semver.
    """
    nums = tuple(int(n) for n in re.findall(r"\d+", tag))
    return (nums, tag)


def list_refs(repo: str, use_cache: bool = True) -> list[str]:
    """A repo's tag names, newest-first (best-effort version sort). Cached ~60s per repo."""
    now = time.monotonic()
    if use_cache:
        cached = _refs_cache.get(repo)
        if cached and cached[0] > now:
            return cached[1]

    # --refs drops the peeled ^{} rows, leaving one entry per tag.
    refs = _parse_ls_remote(_git("ls-remote", "--tags", "--refs", repo_url(repo)))
    tags = [name.removeprefix("refs/tags/") for name in refs]
    tags.sort(key=_version_key, reverse=True)

    _refs_cache[repo] = (now + _REFS_CACHE_TTL, tags)
    return tags
