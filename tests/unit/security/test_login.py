"""The browser login/session primitives (hy-ysn1, F4). Every primitive is fail-closed:
PKCE and the authorize request, the return-to allowlist that refuses an open redirect,
the signed session cookie (tamper/expiry/wrong-key all deny), the cookie attributes,
and the subject-bound CSRF token. No network; pure functions with injected secret/now.
"""

from __future__ import annotations

import base64
import hashlib

import pytest

from hyperset.security import login
from hyperset.security.login import LoginConfigError

SECRET = b"unit-test-signing-secret-of-length"
OTHER_SECRET = b"a-different-signing-secret-000000000"


# --- PKCE --------------------------------------------------------------------


def test_the_code_challenge_is_the_s256_of_the_verifier():
    verifier = login.new_code_verifier()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    assert login.code_challenge(verifier) == expected.decode()
    assert login.CODE_CHALLENGE_METHOD == "S256"


def test_verifiers_and_states_are_unguessable_and_distinct():
    assert login.new_code_verifier() != login.new_code_verifier()
    assert login.new_state() != login.new_state()
    # RFC 7636: 43-128 chars, URL-safe unreserved (no padding).
    verifier = login.new_code_verifier()
    assert 43 <= len(verifier) <= 128
    assert "=" not in verifier


# --- authorize_url -----------------------------------------------------------


def _authorize(**overrides):
    params = {
        "authorization_endpoint": "https://idp.example/authorize",
        "client_id": "hyperset-ui",
        "redirect_uri": "https://app.example/auth/callback",
        "state": "state123",
        "nonce": "nonce123",
        "challenge": "chal123",
    }
    params.update(overrides)
    return login.authorize_url(**params)


def test_authorize_url_carries_the_pkce_and_oidc_params():
    url = _authorize()
    for fragment in (
        "response_type=code",
        "client_id=hyperset-ui",
        "code_challenge=chal123",
        "code_challenge_method=S256",
        "state=state123",
        "nonce=nonce123",
    ):
        assert fragment in url


def test_authorize_url_refuses_a_non_https_endpoint():
    with pytest.raises(LoginConfigError):
        _authorize(authorization_endpoint="http://idp.example/authorize")


def test_authorize_url_refuses_a_blank_required_param():
    for blank in ("client_id", "redirect_uri", "state", "challenge"):
        with pytest.raises(LoginConfigError):
            _authorize(**{blank: "  "})


def test_authorize_url_appends_to_an_endpoint_that_already_has_a_query():
    url = _authorize(authorization_endpoint="https://idp.example/authorize?foo=bar")
    assert "https://idp.example/authorize?foo=bar&response_type=code" in url


# --- return-to allowlist (open-redirect defense) -----------------------------


def test_a_local_absolute_path_is_returned():
    assert login.safe_return_target("/playground/mcp/?x=1") == "/playground/mcp/?x=1"


@pytest.mark.parametrize(
    "hostile",
    [
        "//evil.example/phish",  # protocol-relative -> another origin
        "/\\evil.example",  # backslash trick browsers normalize
        "https://evil.example",  # absolute URL
        "http://evil.example",
        "javascript:alert(1)",  # scheme, not a path
        "evil.example/path",  # no leading slash
        "/path\nSet-Cookie: x",  # control char / header injection
        "/ with space",  # whitespace
    ],
)
def test_a_hostile_return_target_collapses_to_the_default(hostile):
    assert login.safe_return_target(hostile) == "/"
    assert login.safe_return_target(hostile, default="/home") == "/home"


def test_an_empty_or_absent_return_target_is_the_default():
    assert login.safe_return_target(None) == "/"
    assert login.safe_return_target("") == "/"


# --- signed session ----------------------------------------------------------


def _issue(**overrides):
    params = {
        "subject": "user-1",
        "issuer": "https://idp.example/",
        "roles": ("reviewer",),
        "ttl_seconds": 3600,
        "now": 1000,
        "secret": SECRET,
    }
    params.update(overrides)
    return login.issue_session(**params)


def test_a_session_round_trips_its_verified_identity():
    token = _issue()
    session = login.read_session(token, now=1500, secret=SECRET)
    assert session is not None
    assert session.subject == "user-1"
    assert session.issuer == "https://idp.example/"
    assert session.roles == ("reviewer",)
    assert session.expires_at == 4600


def test_a_session_carries_its_verified_workspace(monkeypatch):
    # The signed cookie persists the login's workspace, so a browser session acts in
    # the SAME tenant a bearer would (hq-t6nx). No claim env here, so the round trip
    # does not depend on tenancy config.
    token = _issue(workspace="alpha")
    session = login.read_session(token, now=1500, secret=SECRET)
    assert session is not None and session.workspace == "alpha"
    # An issued session with no explicit workspace is 'default', never a wildcard.
    assert login.read_session(_issue(), now=1500, secret=SECRET).workspace == "default"


def _sign_payload(payload: dict) -> str:
    body = login._b64url(
        __import__("json").dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    return f"{body}.{login._sign(body, SECRET)}"


def test_a_stale_cookie_without_a_workspace_is_rejected_under_configured_tenancy(monkeypatch):
    # A cookie minted before workspace-aware sessions carries no `ws`. Under a
    # deployment that maps a workspace claim it must FAIL CLOSED (never silently act
    # as 'default' in a multi-tenant estate); a single-tenant deployment reads it as
    # 'default' unchanged (hq-t6nx finding 1).
    from hyperset.security.oidc import WORKSPACE_CLAIM_ENV

    stale = _sign_payload({"sub": "u", "iss": "i", "roles": ["reviewer"], "exp": 9999999999})
    assert "ws" not in login._b64url_decode(stale.split(".")[0]).decode()

    monkeypatch.setenv(WORKSPACE_CLAIM_ENV, "hyperset_ws")
    assert login.read_session(stale, now=1500, secret=SECRET) is None  # fail closed

    monkeypatch.delenv(WORKSPACE_CLAIM_ENV, raising=False)
    back_compat = login.read_session(stale, now=1500, secret=SECRET)
    assert back_compat is not None and back_compat.workspace == "default"


def test_principal_from_session_carries_the_workspace(monkeypatch):
    from hyperset.security import authz

    monkeypatch.setenv(authz.ENABLED_ENV, "1")
    principal = login.principal_from_session(_issue(workspace="beta"), now=1500, secret=SECRET)
    assert principal is not None and principal.workspace == "beta"


def test_a_tampered_payload_is_refused():
    token = _issue()
    payload, signature = token.split(".")
    forged = (
        base64.urlsafe_b64encode(b'{"sub":"admin","iss":"i","roles":["admin"],"exp":9999999999}')
        .rstrip(b"=")
        .decode()
    )
    assert login.read_session(f"{forged}.{signature}", now=1500, secret=SECRET) is None


def test_a_wrong_signature_is_refused():
    token = _issue()
    payload, _ = token.split(".")
    assert login.read_session(f"{payload}.not-the-signature", now=1500, secret=SECRET) is None


def test_a_session_signed_by_another_key_is_refused():
    token = _issue(secret=OTHER_SECRET)
    assert login.read_session(token, now=1500, secret=SECRET) is None


def test_an_expired_session_is_refused():
    token = _issue(ttl_seconds=100, now=1000)  # exp = 1100
    assert login.read_session(token, now=1100, secret=SECRET) is None  # at-exp is expired
    assert login.read_session(token, now=1099, secret=SECRET) is not None


def test_issuing_without_a_secret_fails_closed():
    with pytest.raises(LoginConfigError):
        login.issue_session(subject="u", issuer="i", roles=(), secret=None, now=0)


def test_reading_without_a_secret_or_token_is_none():
    assert login.read_session(_issue(), now=1500, secret=None) is None
    assert login.read_session(None, now=1500, secret=SECRET) is None
    assert login.read_session("garbage", now=1500, secret=SECRET) is None


def test_a_session_with_no_subject_is_refused():
    with pytest.raises(LoginConfigError):
        _issue(subject="")


# --- principal_from_session (transport seam) --------------------------------


def test_principal_from_session_is_none_when_the_gate_is_off(monkeypatch):
    from hyperset.security import authz

    monkeypatch.delenv(authz.ENABLED_ENV, raising=False)
    assert login.principal_from_session(_issue(), now=1500, secret=SECRET) is None


def test_principal_from_session_yields_the_verified_principal_when_enabled(monkeypatch):
    from hyperset.security import authz

    monkeypatch.setenv(authz.ENABLED_ENV, "1")
    principal = login.principal_from_session(_issue(), now=1500, secret=SECRET)
    assert principal is not None
    assert principal.subject == "user-1"
    assert principal.roles == ("reviewer",)
    # A tampered cookie is no principal even with the gate on.
    assert login.principal_from_session("x.y", now=1500, secret=SECRET) is None


# --- cookie attributes -------------------------------------------------------


def test_the_session_cookie_is_httponly_lax_and_secure_only_off_loopback():
    secured = login.session_set_cookie("abc", secure=True, max_age=60)
    assert secured.startswith("hyperset_session=abc")
    for attribute in ("HttpOnly", "SameSite=Lax", "Path=/", "Max-Age=60", "Secure"):
        assert attribute in secured
    # Loopback dev: no Secure (a cleartext localhost bind could not send it otherwise).
    local = login.session_set_cookie("abc", secure=False)
    assert "Secure" not in local


def test_logout_clears_the_cookie():
    cleared = login.session_clear_cookie(secure=True)
    assert "hyperset_session=;" in cleared
    assert "Max-Age=0" in cleared
    assert "HttpOnly" in cleared


# --- CSRF --------------------------------------------------------------------


def test_a_csrf_token_verifies_only_for_its_own_subject():
    token = login.issue_csrf("user-1", ttl_seconds=3600, now=1000, secret=SECRET)
    assert login.verify_csrf(token, "user-1", now=1500, secret=SECRET) is True
    # A token issued for another session does not authorize this one.
    assert login.verify_csrf(token, "user-2", now=1500, secret=SECRET) is False


def test_a_csrf_token_fails_closed_on_tamper_expiry_and_wrong_key():
    token = login.issue_csrf("user-1", ttl_seconds=100, now=1000, secret=SECRET)
    assert login.verify_csrf(token, "user-1", now=1101, secret=SECRET) is False  # expired
    assert login.verify_csrf(token, "user-1", now=1050, secret=OTHER_SECRET) is False  # wrong key
    assert login.verify_csrf(token + "x", "user-1", now=1050, secret=SECRET) is False  # tampered
    assert login.verify_csrf(None, "user-1", now=1050, secret=SECRET) is False
    assert login.verify_csrf(token, "", now=1050, secret=SECRET) is False


def test_issuing_csrf_without_a_secret_fails_closed():
    with pytest.raises(LoginConfigError):
        login.issue_csrf("user-1", secret=None, now=0)


# --- the login-transaction cookie (F4 route-wiring, hy-jyha) ---


def test_login_state_round_trips_state_verifier_nonce_and_return():
    token = login.issue_login_state(
        state="s1", code_verifier="v" * 43, nonce="n1", return_to="/admin/", secret=SECRET
    )
    parsed = login.read_login_state(token, secret=SECRET)
    assert parsed is not None
    assert parsed.state == "s1" and parsed.code_verifier == "v" * 43
    assert parsed.nonce == "n1" and parsed.return_to == "/admin/"


def test_login_state_fails_closed_on_tamper_wrong_secret_and_expiry():
    token = login.issue_login_state(
        state="s1", code_verifier="v" * 43, nonce="n1", return_to="/x", secret=SECRET
    )
    assert login.read_login_state(token, secret=OTHER_SECRET) is None  # wrong signer
    assert login.read_login_state(token[:-3] + "zzz", secret=SECRET) is None  # tampered
    assert login.read_login_state(None, secret=SECRET) is None
    assert login.read_login_state("not.a.valid.token", secret=SECRET) is None
    expired = login.issue_login_state(
        state="s1",
        code_verifier="v" * 43,
        nonce="n1",
        return_to="/x",
        ttl_seconds=-1,
        secret=SECRET,
    )
    assert login.read_login_state(expired, secret=SECRET) is None


def test_login_state_return_target_is_reallowlisted_on_read():
    # A hostile return target is collapsed to "/" even if it somehow rode a signed cookie --
    # a layered defense so a login cookie can never become an open redirect.
    token = login.issue_login_state(
        state="s", code_verifier="v" * 43, nonce="n", return_to="//evil.example", secret=SECRET
    )
    assert login.read_login_state(token, secret=SECRET).return_to == "/"


def test_issue_login_state_refuses_a_blank_required_field():
    import pytest

    with pytest.raises(login.LoginConfigError):
        login.issue_login_state(
            state="", code_verifier="v", nonce="n", return_to="/", secret=SECRET
        )


def test_the_login_cookie_is_httponly_and_secure_only_when_asked():
    insecure = login.login_state_set_cookie("val", secure=False)
    assert "HttpOnly" in insecure and "SameSite=Lax" in insecure and "Secure" not in insecure
    secure = login.login_state_set_cookie("val", secure=True)
    assert "Secure" in secure
    cleared = login.login_state_clear_cookie(secure=True)
    assert "Max-Age=0" in cleared and "Secure" in cleared
