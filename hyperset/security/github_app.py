"""GitHub App write-back auth: mint a short-lived installation token (hy-bdhg, ADR 0027).

The enterprise-default write-back path. Hyperset authenticates as a GitHub App and
opens its proposal-only PR as `hyperset[bot]`, using a SHORT-LIVED installation
token minted per operation -- never a stored personal access token.

The ONLY stored secret is the App private key, encrypted at rest via
`hyperset.security.secret_box` (the same AES-256-GCM/KEK path as hy-up4k). At
propose time the server decrypts the key in memory, signs a <10-minute App JWT
(RS256), resolves the App's installation on the target repository, and exchanges
the JWT for an installation token scoped to that repository with
`contents:write` + `pull_requests:write`. The installation token is handed to the
git push child's environment (never argv) and then discarded -- it is never
stored.

Every failure FAILS CLOSED: a bad key, an App not installed on the repository, or
a mint failure raises `GitHubAppError`, and the caller refuses the propose. No
error here carries the private key or the minted token -- only the owner/repo and
the GitHub HTTP status.
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

import jwt
import requests

_API_BASE = "https://api.github.com"
# GitHub caps an App JWT at 10 minutes; stay comfortably under it, and backdate
# `iat` a minute to absorb clock skew between this host and GitHub.
_JWT_LIFETIME_SECONDS = 540
_CLOCK_SKEW_SECONDS = 60
_TIMEOUT_SECONDS = 30


class GitHubAppError(Exception):
    """Minting a GitHub App installation token failed. The caller must FAIL
    CLOSED -- refuse the propose, never push unauthenticated. Its message names
    only the repository and the GitHub HTTP status, never the key or the token."""


def _owner_repo(repository: str) -> tuple[str, str]:
    """The (owner, repo) of a GitHub HTTPS or scp-style remote. A local-path
    target never reaches here (the caller gates on it)."""
    ref = repository.strip()
    if ref.endswith(".git"):
        ref = ref[:-4]
    if ref.startswith(("http://", "https://")):
        path = urlparse(ref).path
    elif "@" in ref and ":" in ref:  # git@github.com:owner/repo
        path = ref.split(":", 1)[1]
    else:
        path = ref
    parts = [segment for segment in path.strip("/").split("/") if segment]
    if len(parts) < 2:
        raise GitHubAppError(
            f"could not parse owner/repo from the write-back repository {repository!r}"
        )
    return parts[-2], parts[-1]


def _app_jwt(app_id: int, private_key: str) -> str:
    """A short-lived RS256 App JWT signed with the decrypted App private key."""
    now = int(time.time())
    payload = {
        # PyJWT requires `iss` to be a string; GitHub accepts the App id either way.
        "iss": str(app_id),
        "iat": now - _CLOCK_SKEW_SECONDS,
        "exp": now + _JWT_LIFETIME_SECONDS,
    }
    try:
        return jwt.encode(payload, private_key, algorithm="RS256")
    except Exception as exc:  # noqa: BLE001 -- any signing failure fails closed
        # The private key never appears in the message -- only that it could not sign.
        raise GitHubAppError(
            "the stored GitHub App private key could not sign a JWT (is it a valid RSA PEM?)"
        ) from exc


def mint_installation_token(
    *,
    app_id: int,
    private_key: str,
    repository: str,
    api_base: str = _API_BASE,
    timeout: int = _TIMEOUT_SECONDS,
) -> str:
    """Mint a repository-scoped installation token for the target repository.

    Signs an App JWT, resolves the App installation on `repository`, and exchanges
    the JWT for an installation token limited to that repository with
    `contents:write` + `pull_requests:write`. Raises `GitHubAppError` (fail
    closed) on a bad key, an App not installed on the repository, or any mint
    failure. The returned token is short-lived (~1 hour) and must never be stored.
    """
    owner, repo = _owner_repo(repository)
    app_token = _app_jwt(app_id, private_key)
    headers = {
        "Authorization": f"Bearer {app_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        found = requests.get(
            f"{api_base}/repos/{owner}/{repo}/installation", headers=headers, timeout=timeout
        )
    except requests.RequestException as exc:
        raise GitHubAppError(
            f"could not reach GitHub to resolve the App installation for {owner}/{repo}"
        ) from exc
    if found.status_code == 404:
        raise GitHubAppError(
            f"the Hyperset GitHub App is not installed on {owner}/{repo}; "
            f"install it on that repository and try again"
        )
    if found.status_code != 200:
        raise GitHubAppError(
            f"could not resolve the GitHub App installation for {owner}/{repo} "
            f"(GitHub returned HTTP {found.status_code})"
        )
    installation_id = found.json().get("id")
    if not installation_id:
        raise GitHubAppError(
            f"the GitHub App installation response for {owner}/{repo} carried no id"
        )
    try:
        minted = requests.post(
            f"{api_base}/app/installations/{installation_id}/access_tokens",
            headers=headers,
            json={
                "repositories": [repo],
                "permissions": {"contents": "write", "pull_requests": "write"},
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise GitHubAppError(
            f"could not reach GitHub to mint an installation token for {owner}/{repo}"
        ) from exc
    if minted.status_code != 201:
        raise GitHubAppError(
            f"could not mint a GitHub App installation token for {owner}/{repo} "
            f"(GitHub returned HTTP {minted.status_code})"
        )
    token = minted.json().get("token")
    if not token:
        raise GitHubAppError(f"the GitHub App token response for {owner}/{repo} carried no token")
    return token
