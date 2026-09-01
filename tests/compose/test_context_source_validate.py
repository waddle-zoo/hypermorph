"""Admin > Context sources > Validate runs its Git op in the api container (hy-jba5i, QA f06ddc9).

The QA repro: Validate FAILED with "git fetch failed because /repo/.runtime/playground-contexts
is not a git repository inside the api container" -- because docker-compose mounted the context
repo (and set the fetch cache dir) ONLY on the migrate service, not on api, so the api container
that serves the admin Validate route had no repo to fetch and no cache to fetch into.

Two levels:
- topology: the api service mirrors the migrate service's context mounts (the fix, guarded by
  config so a future edit that drops the mount reddens without a full stack);
- behaviour: on a real stack, the api container VALIDATES a local Git source under `/repo/...`
  (the exact repro path) as VALID, and reports a genuinely malformed one as invalid -- proving
  the git op actually runs there, not merely that a mount is declared.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

from tests.compose.conftest import REPO_ROOT
from tests.integration.test_git_context_source import CONTEXT_PATH, FIXTURE_DIR, git


def _compose(*args, env, timeout=180):
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _context_mounts(service: dict) -> set[str]:
    """The (source, target, ro) of every bind/volume mount on a compose service, normalised."""
    mounts = set()
    for volume in service.get("volumes", []):
        mounts.add((volume.get("source"), volume.get("target"), bool(volume.get("read_only"))))
    return mounts


@pytest.mark.compose
def test_the_api_service_mirrors_the_migrate_context_topology(compose_env):
    """The FIX, guarded at config level: the api service carries the same `/repo` checkout, the
    same fetch cache volume, and the same HYPERSET_CONTEXT_CACHE_DIR as migrate -- so the admin
    Validate/Sync git op the api serves has a repo to fetch and a cache to fetch into. If a
    future edit drops the mount, this reddens without needing the whole stack."""
    result = _compose("config", "--format", "json", env=compose_env)
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    api = config["services"]["api"]
    migrate = config["services"]["hyperset-migrate"]

    # The api must carry migrate's context mounts (the /repo checkout + the fetch cache volume).
    assert _context_mounts(migrate) <= _context_mounts(api), (
        f"api is missing migrate's context mounts: migrate={_context_mounts(migrate)} "
        f"api={_context_mounts(api)}"
    )
    # The /repo checkout is read-only on api too (fetch-only; the Git-authority boundary holds).
    repo_mounts = {(s, t, ro) for (s, t, ro) in _context_mounts(api) if t == "/repo"}
    assert repo_mounts and all(ro for (_s, _t, ro) in repo_mounts), (
        f"api's /repo mount must be read-only (fetch-only): {repo_mounts}"
    )
    # And the same cache dir env, so the fetch mirror lands in the shared volume.
    assert api["environment"].get("HYPERSET_CONTEXT_CACHE_DIR") == migrate["environment"].get(
        "HYPERSET_CONTEXT_CACHE_DIR"
    )


def _seed_context_repo(name: str, *, valid: bool) -> str:
    """A real Git repo under REPO_ROOT/.runtime (so the api's `/repo:ro` mount reaches it at
    `/repo/.runtime/<name>`, the exact QA repro path). Returns the in-container repository path."""
    root = REPO_ROOT / ".runtime" / name
    if root.exists():
        shutil.rmtree(root)
    (root / CONTEXT_PATH).mkdir(parents=True)
    git("init", "--quiet", "--initial-branch=main", ".", cwd=root)
    git("config", "user.email", "context@example.test", cwd=root)
    git("config", "user.name", "Context Owner", cwd=root)
    for path in sorted(FIXTURE_DIR.iterdir()):
        shutil.copy(path, root / CONTEXT_PATH / path.name)
    if not valid:
        # A genuinely malformed manifest (no `domain`) -- validation must report INVALID for a
        # real reason, distinct from the missing-repo failure the fix removes.
        (root / CONTEXT_PATH / "manifest.yaml").write_text("title: broken\n", encoding="utf-8")
    git("add", "-A", cwd=root)
    git("commit", "--quiet", "-m", "seed context", cwd=root)
    return f"/repo/.runtime/{name}"


def _api_base(env) -> str:
    port = _compose("port", "api", "8080", env=env).stdout.strip().rsplit(":", 1)[-1]
    return f"http://127.0.0.1:{port}"


def _post(base: str, path: str, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


@pytest.mark.compose
def test_the_api_container_validates_a_local_git_source_under_repo(compose_env):
    """The behaviour the topology fix exists for: the api container performs the source-Validate
    Git fetch against a `/repo/.runtime/...` repository (the QA repro path) and returns VALID for
    a healthy source, INVALID (for a real reason) for a malformed one -- never the pre-fix
    "not a git repository" failure."""
    env = dict(compose_env)
    env["HYPERSET_PLAYGROUND_ENABLED"] = "true"  # the admin context routes are playground-gated

    good_repo = _seed_context_repo(f"validate-good-{uuid.uuid4().hex[:8]}", valid=True)
    bad_repo = _seed_context_repo(f"validate-bad-{uuid.uuid4().hex[:8]}", valid=False)
    try:
        assert _compose("up", "-d", "--wait", "postgres", env=env, timeout=120).returncode == 0
        assert _compose("run", "--rm", "hyperset-migrate", env=env, timeout=120).returncode == 0
        up = _compose("up", "-d", "--wait", "api", env=env, timeout=240)
        assert up.returncode == 0, up.stderr
        base = _api_base(env)
        sources = "/admin/api/v0/context/sources"

        # Healthy source under /repo/.runtime/... -> VALID (the api container did the git fetch).
        status, added = _post(base, sources, {"repository": good_repo, "path": CONTEXT_PATH})
        assert status == 200, added
        status, valid = _post(
            base, f"{sources}/validate", {"source_id": added["source"]["source_id"]}
        )
        assert status == 200, valid
        blob = json.dumps(valid)
        assert "not a git repository" not in blob, valid  # the exact repro is gone
        assert valid["result"]["status"] == "valid", valid

        # A malformed manifest -> INVALID for a REAL reason, not the missing-repo failure.
        status, added_bad = _post(base, sources, {"repository": bad_repo, "path": CONTEXT_PATH})
        assert status == 200, added_bad
        status, invalid = _post(
            base, f"{sources}/validate", {"source_id": added_bad["source"]["source_id"]}
        )
        assert status == 200, invalid
        assert invalid["result"]["status"] == "invalid", invalid
        assert "not a git repository" not in json.dumps(invalid), invalid
    finally:
        _compose("down", "-v", env=env)
        for repo in (good_repo, bad_repo):
            shutil.rmtree(REPO_ROOT / Path(repo).relative_to("/repo"), ignore_errors=True)
