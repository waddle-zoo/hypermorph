"""GitHub App installation-token mint (hy-bdhg, ADR 0027).

The mint signs a short-lived App JWT with a test RSA key and exchanges it for an
installation token against a MOCKED GitHub API. Every failure fails closed with a
`GitHubAppError`, and no error carries the private key.
"""

from __future__ import annotations

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from hyperset.security import github_app
from hyperset.security.github_app import GitHubAppError, mint_installation_token


def _rsa_pem() -> tuple[str, str]:
    """A throwaway RSA keypair: (private PEM, public PEM)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private_pem, public_pem


class _FakeResponse:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict:
        return self._body


class _FakeRequests:
    """Records the calls and returns queued responses in order per verb."""

    def __init__(self, get: list[_FakeResponse], post: list[_FakeResponse]) -> None:
        self._get = list(get)
        self._post = list(post)
        self.get_calls: list[dict] = []
        self.post_calls: list[dict] = []
        # The real module raises requests.RequestException; keep the type available.
        self.RequestException = github_app.requests.RequestException

    def get(self, url, headers=None, timeout=None):
        self.get_calls.append({"url": url, "headers": headers, "timeout": timeout})
        return self._get.pop(0)

    def post(self, url, headers=None, json=None, timeout=None):
        self.post_calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return self._post.pop(0)


def _install(monkeypatch, fake: _FakeRequests) -> None:
    monkeypatch.setattr(github_app, "requests", fake)


def test_mint_signs_a_short_lived_jwt_and_returns_the_installation_token(monkeypatch):
    private_pem, public_pem = _rsa_pem()
    fake = _FakeRequests(
        get=[_FakeResponse(200, {"id": 4242})],
        post=[_FakeResponse(201, {"token": "ghs_installation_token"})],
    )
    _install(monkeypatch, fake)

    token = mint_installation_token(
        app_id=99, private_key=private_pem, repository="https://github.com/acme/context"
    )

    assert token == "ghs_installation_token"
    # Installation resolved for the parsed owner/repo, token minted for its id.
    assert fake.get_calls[0]["url"].endswith("/repos/acme/context/installation")
    assert fake.post_calls[0]["url"].endswith("/app/installations/4242/access_tokens")
    # Repository-scoped, least privilege for a proposal-only PR.
    assert fake.post_calls[0]["json"] == {
        "repositories": ["context"],
        "permissions": {"contents": "write", "pull_requests": "write"},
    }
    # The Authorization header is a verifiable RS256 App JWT, short-lived.
    bearer = fake.get_calls[0]["headers"]["Authorization"]
    assert bearer.startswith("Bearer ")
    claims = jwt.decode(bearer[len("Bearer ") :], public_pem, algorithms=["RS256"])
    assert claims["iss"] == "99"
    assert 0 < claims["exp"] - claims["iat"] <= 600  # under GitHub's 10-minute cap


def test_mint_fails_closed_when_the_app_is_not_installed(monkeypatch):
    private_pem, _ = _rsa_pem()
    fake = _FakeRequests(get=[_FakeResponse(404, {})], post=[])
    _install(monkeypatch, fake)

    with pytest.raises(GitHubAppError) as excinfo:
        mint_installation_token(
            app_id=1, private_key=private_pem, repository="https://github.com/acme/context"
        )
    assert "not installed" in str(excinfo.value)
    assert "acme/context" in str(excinfo.value)
    assert not fake.post_calls  # never tried to mint


def test_mint_fails_closed_when_the_token_exchange_is_rejected(monkeypatch):
    private_pem, _ = _rsa_pem()
    fake = _FakeRequests(get=[_FakeResponse(200, {"id": 7})], post=[_FakeResponse(403, {})])
    _install(monkeypatch, fake)

    with pytest.raises(GitHubAppError) as excinfo:
        mint_installation_token(
            app_id=1, private_key=private_pem, repository="https://github.com/acme/context"
        )
    assert "could not mint" in str(excinfo.value)
    assert "403" in str(excinfo.value)


def test_mint_fails_closed_on_an_invalid_private_key_without_leaking_it(monkeypatch):
    # Never reaches the network -- signing fails first.
    fake = _FakeRequests(get=[], post=[])
    _install(monkeypatch, fake)
    bogus = "-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n-----END PRIVATE KEY-----"

    with pytest.raises(GitHubAppError) as excinfo:
        mint_installation_token(
            app_id=1, private_key=bogus, repository="https://github.com/acme/context"
        )
    message = str(excinfo.value)
    assert "could not sign" in message
    assert "not-a-real-key" not in message  # the key never appears in the error
    assert not fake.get_calls and not fake.post_calls


@pytest.mark.parametrize(
    "repository,expected",
    [
        ("https://github.com/acme/context", ("acme", "context")),
        ("https://github.com/acme/context.git", ("acme", "context")),
        ("git@github.com:acme/context.git", ("acme", "context")),
        ("https://ghe.example.com/org/team/repo", ("team", "repo")),
    ],
)
def test_owner_repo_parses_common_remote_forms(repository, expected):
    assert github_app._owner_repo(repository) == expected


def test_owner_repo_rejects_a_remote_without_owner_and_repo():
    with pytest.raises(GitHubAppError):
        github_app._owner_repo("https://github.com/onlyone")
