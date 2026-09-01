"""Browser login/session primitives for exposed UIs (hy-ysn1, F4 under overseer
ruling hq-l4g2). A browser cannot send a bearer JWT it never obtained, so an
exposed UI needs an OIDC Authorization-Code + PKCE login and a server-set session.

This module is the PURE, fail-closed core of that flow -- PKCE, the state/nonce
random tokens, the authorize-URL builder, a local-only return-to allowlist (no open
redirect), a signed session cookie with the right browser attributes, a CSRF
token for cookie-auth mutations, and (F4 route-wiring, hy-jyha) the signed short-lived
login-transaction cookie that carries state/PKCE/return-to from /login to /callback.
It performs NO network I/O: the code->token exchange that the login routes need is in
`oidc.py` (which already owns the HTTPS-only opener), and the served routes themselves
(login/callback/logout, the login button) live in the transport (hy-jyha). Off the authz
flag every primitive here is inert, exactly as before.

Everything here fails closed: an unset signing secret, a tampered or expired
cookie, a CSRF token from another session, a non-HTTPS authorization endpoint, or
a cross-site return target all yield "no session"/"refused", never a trusted
identity. No token or secret is ever placed in a URL, a log, or a browser-readable
store: the session cookie is HttpOnly and its value is an opaque signed blob of the
already-verified identity (subject/issuer/roles), never the IdP tokens themselves.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from urllib.parse import urlencode

from hyperset.security.authz import Principal, authz_enabled

# The HMAC key that signs the session and CSRF tokens. Read from the environment,
# never the database or a request; absent, the signing/verifying primitives fail
# closed (no session is issued or accepted) rather than sign with a guessable key.
SESSION_SECRET_ENV = "HYPERSET_SESSION_SECRET"

# The OIDC client config the authorize-URL builder needs. Provider-neutral, read from
# the environment like the verifier's issuer/audience/JWKS (oidc.py).
AUTHORIZATION_ENDPOINT_ENV = "HYPERSET_OIDC_AUTHORIZATION_ENDPOINT"
CLIENT_ID_ENV = "HYPERSET_OIDC_CLIENT_ID"
REDIRECT_URI_ENV = "HYPERSET_OIDC_REDIRECT_URI"
SCOPES_ENV = "HYPERSET_OIDC_SCOPES"

CODE_CHALLENGE_METHOD = "S256"
DEFAULT_SCOPES = "openid profile email"
SESSION_COOKIE_NAME = "hyperset_session"
DEFAULT_SESSION_TTL_SECONDS = 8 * 3600
DEFAULT_CSRF_TTL_SECONDS = 8 * 3600


def _b64url(raw: bytes) -> str:
    """URL-safe base64 with no padding -- the encoding PKCE (RFC 7636) and JWT use."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


# --- PKCE (RFC 7636) ---------------------------------------------------------


def new_code_verifier() -> str:
    """A fresh PKCE code verifier: 43 chars of URL-safe base64 over 32 random bytes,
    within the RFC's 43-128 unreserved-character range."""
    return _b64url(os.urandom(32))


def code_challenge(verifier: str) -> str:
    """The S256 challenge for a verifier: base64url(SHA-256(verifier)). The server
    keeps the verifier (in a short-lived cookie) and sends only this challenge, so an
    attacker who intercepts the authorization request cannot replay the code."""
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def new_state() -> str:
    """A random, unguessable value for the `state`/`nonce` parameters -- CSRF and
    replay protection on the authorization request, compared on the callback."""
    return _b64url(os.urandom(32))


# --- The authorization request ----------------------------------------------


def authorize_url(
    *,
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    nonce: str,
    challenge: str,
    scope: str = DEFAULT_SCOPES,
) -> str:
    """Build the OIDC Authorization-Code + PKCE request URL, or refuse. The endpoint
    MUST be HTTPS (an authorization request over cleartext exposes the whole login);
    a non-HTTPS endpoint raises rather than downgrade. All parameters are required and
    non-empty -- a blank client_id or redirect_uri is a misconfiguration, not a login."""
    for name, value in (
        ("authorization_endpoint", authorization_endpoint),
        ("client_id", client_id),
        ("redirect_uri", redirect_uri),
        ("state", state),
        ("nonce", nonce),
        ("challenge", challenge),
    ):
        if not (value and value.strip()):
            raise LoginConfigError(f"{name} is required for the authorization request")
    if not authorization_endpoint.lower().startswith("https://"):
        raise LoginConfigError("the OIDC authorization endpoint must be https")
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": CODE_CHALLENGE_METHOD,
        }
    )
    separator = "&" if "?" in authorization_endpoint else "?"
    return f"{authorization_endpoint}{separator}{query}"


class LoginConfigError(RuntimeError):
    """A login primitive asked to act on absent/invalid configuration. Raised rather
    than returning a value so a misconfigured login cannot silently proceed."""


# --- Return-to (deep link) allowlist ----------------------------------------


def safe_return_target(candidate: str | None, *, default: str = "/") -> str:
    """A LOCAL absolute path to return to after login, or the default -- never an open
    redirect. Only a path that starts with a single `/` (not `//` or `/\\`, which are
    protocol-relative or backslash tricks a browser resolves to another origin), with
    no control characters or whitespace, is returned; anything else -- an absolute URL,
    a scheme, `javascript:`, or junk -- collapses to the default. Deep links are
    preserved WITHOUT letting the `state`/`return` parameter bounce a user off-site."""
    if not candidate:
        return default
    if not candidate.startswith("/"):
        return default
    if candidate.startswith(("//", "/\\")):
        return default
    if any(ord(character) < 0x20 or character.isspace() for character in candidate):
        return default
    return candidate


# --- The signed session ------------------------------------------------------


@dataclass(frozen=True)
class Session:
    """The already-verified identity a session cookie carries. It is the OUTPUT of a
    completed login (the IdP tokens were verified server-side); the cookie never holds
    the IdP tokens themselves, only this."""

    subject: str
    issuer: str
    roles: tuple[str, ...]
    expires_at: int
    # The tenant/workspace the login verified (hq-t6nx, ADR-0037), persisted in the
    # signed cookie so a browser session carries the SAME workspace a bearer would.
    # Without it every cookie-auth caller would silently reconstruct 'default'.
    workspace: str = "default"


def _tenancy_configured() -> bool:
    """Whether this deployment maps a workspace claim (hq-t6nx). When it does, a
    session that predates workspace-aware cookies (no `ws`) must be REJECTED rather
    than treated as 'default', so a stale cookie cannot act in the default tenant."""
    from hyperset.security.oidc import WORKSPACE_CLAIM_ENV

    return bool(os.environ.get(WORKSPACE_CLAIM_ENV, "").strip())


def _secret() -> bytes | None:
    raw = os.environ.get(SESSION_SECRET_ENV, "").strip()
    return raw.encode("utf-8") if raw else None


def _sign(payload_b64: str, secret: bytes) -> str:
    return _b64url(hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).digest())


def issue_session(
    *,
    subject: str,
    issuer: str,
    roles: tuple[str, ...],
    workspace: str = "default",
    ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
    now: int | None = None,
    secret: bytes | None = None,
) -> str:
    """Sign a session token for a verified identity, INCLUDING its workspace (hq-t6nx).
    Fails closed on an absent signing secret (raises) rather than issue an unsigned or
    guessably-signed session."""
    key = secret if secret is not None else _secret()
    if key is None:
        raise LoginConfigError(
            f"{SESSION_SECRET_ENV} is unset; refusing to issue an unsigned session"
        )
    if not subject:
        raise LoginConfigError("a session needs a subject")
    issued_at = int(now if now is not None else time.time())
    payload = {
        "sub": subject,
        "iss": issuer,
        "roles": list(roles),
        "ws": workspace or "default",
        "exp": issued_at + int(ttl_seconds),
    }
    payload_b64 = _b64url(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    return f"{payload_b64}.{_sign(payload_b64, key)}"


def read_session(
    token: str | None,
    *,
    now: int | None = None,
    secret: bytes | None = None,
) -> Session | None:
    """The verified `Session` a token carries, or `None` on ANY failure: an absent
    token, an absent secret, a malformed token, a signature that does not match (checked
    in constant time), an expired token, or a payload with no subject. Fail-closed, so a
    tampered or stale cookie is simply not a session."""
    key = secret if secret is not None else _secret()
    if not token or key is None:
        return None
    parts = token.split(".")
    if len(parts) != 2:
        return None
    payload_b64, signature = parts
    expected = _sign(payload_b64, key)
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    subject = payload.get("sub")
    expires_at = payload.get("exp")
    if not isinstance(subject, str) or not subject:
        return None
    if not isinstance(expires_at, int) or isinstance(expires_at, bool):
        return None
    current = int(now if now is not None else time.time())
    if current >= expires_at:
        return None
    raw_roles = payload.get("roles")
    roles = (
        tuple(role for role in raw_roles if isinstance(role, str))
        if isinstance(raw_roles, list)
        else ()
    )
    # The workspace the login verified (hq-t6nx). A session that predates
    # workspace-aware cookies has no `ws`; under CONFIGURED tenancy that stale
    # cookie is REJECTED (fail closed -- it must not act as 'default' in another
    # tenant's deployment), while a single-tenant deployment reads it as 'default'.
    raw_ws = payload.get("ws")
    workspace = raw_ws.strip() if isinstance(raw_ws, str) and raw_ws.strip() else None
    if workspace is None:
        if _tenancy_configured():
            return None
        workspace = "default"
    return Session(
        subject=subject,
        issuer=str(payload.get("iss") or ""),
        roles=roles,
        expires_at=expires_at,
        workspace=workspace,
    )


def principal_from_session(
    token: str | None,
    *,
    now: int | None = None,
    secret: bytes | None = None,
) -> Principal | None:
    """The verified caller behind a session cookie, or `None`. Returns `None` when the
    authz gate is off (no session work off the flag), mirroring `principal_from_bearer`:
    a transport reads its session cookie through this and passes the result into
    `run_operation`, and it constructs a `Principal` in no transport."""
    if not authz_enabled():
        return None
    session = read_session(token, now=now, secret=secret)
    if session is None:
        return None
    return Principal(
        subject=session.subject,
        issuer=session.issuer,
        roles=session.roles,
        workspace=session.workspace,
    )


# --- Session cookie attributes ----------------------------------------------


def session_set_cookie(
    value: str,
    *,
    secure: bool,
    max_age: int = DEFAULT_SESSION_TTL_SECONDS,
    name: str = SESSION_COOKIE_NAME,
) -> str:
    """The `Set-Cookie` header for a session. HttpOnly (never readable by page JS, so a
    script injection cannot exfiltrate it), SameSite=Lax (sent on the top-level IdP
    callback redirect, not on cross-site subresource requests), Path=/, a bounded
    Max-Age, and Secure whenever the bind is not loopback (so it is never sent over
    cleartext in production)."""
    parts = [
        f"{name}={value}",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        f"Max-Age={int(max_age)}",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def session_clear_cookie(*, secure: bool, name: str = SESSION_COOKIE_NAME) -> str:
    """The `Set-Cookie` header that ends a session (logout): the same attributes with
    an empty value and Max-Age=0, so the browser drops it immediately."""
    parts = [f"{name}=", "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=0"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


# --- CSRF for cookie-auth mutations -----------------------------------------


def issue_csrf(
    subject: str,
    *,
    ttl_seconds: int = DEFAULT_CSRF_TTL_SECONDS,
    now: int | None = None,
    secret: bytes | None = None,
) -> str:
    """A CSRF token BOUND to the session subject and signed, so a mutation authorized by
    a session cookie must also present a token this server issued for THAT session -- a
    cross-site form post carrying the cookie but no valid token is refused. Fails closed
    on an absent secret."""
    key = secret if secret is not None else _secret()
    if key is None:
        raise LoginConfigError(f"{SESSION_SECRET_ENV} is unset; refusing to issue a CSRF token")
    if not subject:
        raise LoginConfigError("a CSRF token needs a subject")
    expires_at = int(now if now is not None else time.time()) + int(ttl_seconds)
    payload_b64 = _b64url(f"{subject}:{expires_at}".encode())
    return f"{payload_b64}.{_sign(payload_b64, key)}"


def verify_csrf(
    token: str | None,
    subject: str,
    *,
    now: int | None = None,
    secret: bytes | None = None,
) -> bool:
    """Whether `token` is a live CSRF token this server issued for `subject`. Fail-closed
    on an absent token/secret/subject, a bad signature (constant-time), a subject that
    does not match the session's, expiry, or any malformation."""
    key = secret if secret is not None else _secret()
    if not token or key is None or not subject:
        return False
    parts = token.split(".")
    if len(parts) != 2:
        return False
    payload_b64, signature = parts
    if not hmac.compare_digest(_sign(payload_b64, key), signature):
        return False
    try:
        decoded = _b64url_decode(payload_b64).decode("utf-8")
        token_subject, _, expires_raw = decoded.rpartition(":")
        expires_at = int(expires_raw)
    except (ValueError, UnicodeDecodeError):
        return False
    if token_subject != subject:
        return False
    current = int(now if now is not None else time.time())
    return current < expires_at


# --- The login-transaction cookie (F4 route-wiring, hy-jyha) -----------------
#
# The /login route generates PKCE + state + nonce + the deep-link return target and must
# hand them to the /callback route WITHOUT a server-side store. It signs them into a short-
# lived, HttpOnly cookie (the "login transaction"), the same HMAC the session uses. The
# callback reads it back, checks the IdP's `state` matches, and uses `code_verifier` for the
# PKCE exchange. Fail-closed: a tampered, expired, or absent cookie is simply "no login in
# progress". The verifier is a SECRET (it proves the code belongs to this browser), so the
# cookie is HttpOnly and never placed in a URL or log.

LOGIN_STATE_COOKIE_NAME = "hyperset_login"
DEFAULT_LOGIN_STATE_TTL_SECONDS = 600  # 10 minutes -- a login should complete quickly


@dataclass(frozen=True)
class LoginState:
    """The in-flight login a transaction cookie carries between /login and /callback."""

    state: str
    code_verifier: str
    nonce: str
    return_to: str
    expires_at: int


def issue_login_state(
    *,
    state: str,
    code_verifier: str,
    nonce: str,
    return_to: str,
    ttl_seconds: int = DEFAULT_LOGIN_STATE_TTL_SECONDS,
    now: int | None = None,
    secret: bytes | None = None,
) -> str:
    """Sign the login-transaction into a cookie value. Fails closed on an absent signing
    secret; every field is required (a login with no state or verifier is not a login)."""
    key = secret if secret is not None else _secret()
    if key is None:
        raise LoginConfigError(f"{SESSION_SECRET_ENV} is unset; refusing to sign a login state")
    for name, value in (
        ("state", state),
        ("code_verifier", code_verifier),
        ("nonce", nonce),
    ):
        if not value:
            raise LoginConfigError(f"{name} is required for a login transaction")
    issued_at = int(now if now is not None else time.time())
    payload = {
        "st": state,
        "cv": code_verifier,
        "no": nonce,
        "rt": return_to,
        "exp": issued_at + int(ttl_seconds),
    }
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    return f"{payload_b64}.{_sign(payload_b64, key)}"


def read_login_state(
    token: str | None, *, now: int | None = None, secret: bytes | None = None
) -> LoginState | None:
    """The `LoginState` a transaction cookie carries, or `None` on ANY failure (absent
    token/secret, malformed, bad signature checked in constant time, expired, or missing a
    field). Fail-closed, so a tampered or stale login cookie is simply no login."""
    key = secret if secret is not None else _secret()
    if not token or key is None:
        return None
    parts = token.split(".")
    if len(parts) != 2:
        return None
    payload_b64, signature = parts
    if not hmac.compare_digest(_sign(payload_b64, key), signature):
        return None
    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    state = payload.get("st")
    verifier = payload.get("cv")
    nonce = payload.get("no")
    expires_at = payload.get("exp")
    if not (isinstance(state, str) and state and isinstance(verifier, str) and verifier):
        return None
    if not isinstance(nonce, str) or not nonce:
        return None
    if not isinstance(expires_at, int) or isinstance(expires_at, bool):
        return None
    if int(now if now is not None else time.time()) >= expires_at:
        return None
    # The stored return target was already allow-listed at /login; re-run the filter so a
    # tampering that somehow survived the signature cannot become an open redirect.
    return LoginState(
        state=state,
        code_verifier=verifier,
        nonce=nonce,
        return_to=safe_return_target(
            payload.get("rt") if isinstance(payload.get("rt"), str) else None
        ),
        expires_at=expires_at,
    )


def login_state_set_cookie(value: str, *, secure: bool) -> str:
    """The `Set-Cookie` for the login transaction: HttpOnly, SameSite=Lax (returned on the
    top-level IdP redirect back to /callback), short Max-Age, Secure off loopback."""
    parts = [
        f"{LOGIN_STATE_COOKIE_NAME}={value}",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        f"Max-Age={DEFAULT_LOGIN_STATE_TTL_SECONDS}",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def login_state_clear_cookie(*, secure: bool) -> str:
    """Drop the login-transaction cookie (it is single-use: cleared once /callback consumes
    it, so a captured cookie cannot be replayed)."""
    parts = [f"{LOGIN_STATE_COOKIE_NAME}=", "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=0"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)
