"""Fail-closed OIDC bearer-token verification for the authorization gate
(hy-ac2x, ADR-0030). Provider-neutral: issuer, audience, JWKS URL, and the roles
claim are read from the environment (Okta is a configuration target, not a special
case). PyJWT verifies the token against the provider's JWKS; ANY failure -- no token,
a bad signature, a wrong issuer/audience, an expired token, a misconfigured verifier
-- yields no principal, so the gate denies. Off entirely unless HYPERSET_AUTHZ_ENABLED.

Verification lives here, in ONE audited function, rather than in each transport: a
transport hands the raw `Authorization` header to `principal_from_bearer` and receives
a verified `Principal` or `None`. The gate at `run_operation` then makes the authz
DECISION over that principal. Nothing here enforces; it authenticates.
"""

from __future__ import annotations

import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from hyperset.security.authz import (
    CLIENT_CREDENTIALS_ONLY_ROLES,
    ENABLED_ENV,
    Principal,
    authz_enabled,
)

ISSUER_ENV = "HYPERSET_OIDC_ISSUER"
AUDIENCE_ENV = "HYPERSET_OIDC_AUDIENCE"
JWKS_ENV = "HYPERSET_OIDC_JWKS_URL"
ROLES_CLAIM_ENV = "HYPERSET_OIDC_ROLES_CLAIM"
# The claim naming the caller's TENANT/WORKSPACE (hq-t6nx, ADR-0037). Optional:
# unset keeps the single implicit `"default"` workspace. Once configured, a token
# that lacks a valid claim is denied rather than silently entering that workspace.
WORKSPACE_CLAIM_ENV = "HYPERSET_OIDC_WORKSPACE_CLAIM"
DEFAULT_WORKSPACE = "default"
# The token endpoint the F4 login callback exchanges an authorization code at, and an
# OPTIONAL confidential-client secret (read server-side, never in a URL/browser/log). A
# public PKCE client needs no secret; a confidential one sends it in the token request.
TOKEN_ENDPOINT_ENV = "HYPERSET_OIDC_TOKEN_ENDPOINT"
CLIENT_SECRET_ENV = "HYPERSET_OIDC_CLIENT_SECRET"

# The claims that carry the OAuth2 CLIENT ID of the party a token was issued to. RFC 9068
# ("JWT Profile for OAuth 2.0 Access Tokens") defines `client_id`; OIDC defines `azp` (the
# authorized party). For a CLIENT-CREDENTIALS grant there is no distinct end user, so the
# subject IS the client (`sub == client_id`); for a human AUTHORIZATION-CODE token the
# subject is the user and the client id is the app, so they DIFFER. That equality is the
# provider-neutral discriminator (hy-okm6); `client_id` is checked before `azp`.
_CLIENT_ID_CLAIMS = ("client_id", "azp")

# The config flag `authz_enabled` (and its `ENABLED_ENV`) live with the authz model
# in `security/authz`, so a caller can ask "is the gate on?" without importing the JWT
# verifier; both are re-exported here because `principal_from_bearer` is where a
# transport reaches them.
__all__ = ["ENABLED_ENV", "authz_enabled", "verify_bearer", "principal_from_bearer"]


def _cfg(name: str) -> str:
    return os.environ.get(name, "").strip()


def _roles_from_claims(claims: dict) -> tuple[str, ...]:
    """The verified caller's role NAMES, derived from the configured roles claim
    (hy-09hy, F2 under overseer ruling hq-l4g2). This authenticates and CARRIES the
    claimed roles; it does not authorize -- `authz.authorize` resolves each name
    against the role registry, and a name the registry does not know matches no grant
    (fail closed), so an attacker-influenced claim cannot invent authority.

    Three cases, all fail-closed:

    - The roles claim is NOT configured (`HYPERSET_OIDC_ROLES_CLAIM` unset): role
      mapping is opted out, and a verified caller gets the baseline `reader` role --
      exactly as before this slice, so an enabled deployment that has not configured a
      claim is byte-identical.
    - The claim IS configured and the token carries role names (a JSON array of
      strings, or a single space/comma-delimited string): those names, de-duplicated
      in order, empties dropped.
    - The claim is configured but the token has no valid roles (absent, empty, or a
      non-string/array value): NO roles -> `authorize` denies everything. Least
      privilege: a verified identity with no mapped role is authenticated, not
      authorized. Reader is granted ONLY on the opt-out path above, never as a silent
      fallback when a configured claim is missing.
    """
    claim_name = _cfg(ROLES_CLAIM_ENV)
    if not claim_name:
        return ("reader",)
    raw = claims.get(claim_name)
    if isinstance(raw, str):
        values: list = raw.replace(",", " ").split()
    elif isinstance(raw, list | tuple):
        values = [item for item in raw if isinstance(item, str)]
    else:
        values = []
    deduped: dict[str, None] = {}
    for value in values:
        cleaned = value.strip()
        if cleaned:
            deduped.setdefault(cleaned, None)
    roles = tuple(deduped)
    # MACHINE-ONLY roles (`service`) are honored ONLY for a genuine client-credentials
    # token (hy-okm6). A human bearer that lists `roles=["service"]` -- or
    # `["service","reviewer"]` -- must not become a service identity: strip any
    # client-credentials-only name unless the token proves it is a client-credentials
    # grant, leaving the caller's human roles intact. Fail closed: if we cannot prove
    # client-credentials, the privileged name is dropped.
    if not _is_client_credentials(claims):
        roles = tuple(name for name in roles if name not in CLIENT_CREDENTIALS_ONLY_ROLES)
    return roles


def _workspace_from_claims(claims: dict) -> str | None:
    """The caller's TENANT/WORKSPACE, from the configured claim (hq-t6nx, ADR-0037).

    If the deployment does not configure a claim, use the single implicit default
    workspace. If it does, a missing, blank, or malformed claim returns ``None``
    so authentication fails closed instead of granting access to default-tenant data.
    """
    claim_name = _cfg(WORKSPACE_CLAIM_ENV)
    if not claim_name:
        return DEFAULT_WORKSPACE
    raw = claims.get(claim_name)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _is_client_credentials(claims: dict) -> bool:
    """Whether a verified token was issued via the OAuth2 CLIENT-CREDENTIALS grant (a
    non-human caller), the fail-closed discriminator that gates the machine-only `service`
    role (hy-okm6). TRUE only with POSITIVE proof: the token carries a client-id claim
    (`client_id` per RFC 9068, or `azp`) that is present and EQUAL to `sub` -- the
    defining shape of a token with no distinct end user (the subject is the client
    itself). A human authorization-code token has `sub` (the user) != the client id and
    so is never client-credentials. An absent/empty/mismatched client id is not proof, so
    it fails closed and the privileged role is denied."""
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        return False
    for name in _CLIENT_ID_CLAIMS:
        client_id = claims.get(name)
        if isinstance(client_id, str) and client_id and client_id == subject:
            return True
    return False


class _HTTPSOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse a JWKS redirect whose target is not `https://`. The configured JWKS URL is
    checked to be https BEFORE the fetch, but a `PyJWKClient` fetch follows 3xx redirects
    by default -- an attacker able to answer the first hop could 302 to an `http://`
    location and serve substituted signing keys over cleartext (a MITM re-entry past the
    initial https check). Refusing any downgrade keeps the ENTIRE signing-key fetch on
    https; a legitimate https->https redirect is still followed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise urllib.error.HTTPError(
                newurl, code, "refused a JWKS redirect to a non-HTTPS URL", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# The opener the JWKS fetch runs through: the default handlers (including HTTPS cert
# validation) with the redirect handler REPLACED by the https-only one above.
_JWKS_OPENER = urllib.request.build_opener(_HTTPSOnlyRedirectHandler)

_hardened_jwk_client_cls = None


def _redirect_hardened_jwk_client_cls():
    """A `PyJWKClient` subclass whose `fetch_data` retrieves the JWKS through the
    https-only opener, so signing keys are never fetched over cleartext -- not even
    after a redirect. Built once, lazily, so importing this module does not import
    PyJWT (kept lazy exactly as `verify_bearer` does)."""
    global _hardened_jwk_client_cls
    if _hardened_jwk_client_cls is not None:
        return _hardened_jwk_client_cls

    import jwt

    class _RedirectHardenedJWKClient(jwt.PyJWKClient):
        def fetch_data(self):
            # Mirrors PyJWKClient.fetch_data but through `_JWKS_OPENER` so a redirect
            # off https is refused. The opener's default HTTPSHandler still validates
            # certs (CERT_REQUIRED); `self.ssl_context` is intentionally not threaded
            # here because `verify_bearer` never sets one and the default is validating.
            request = urllib.request.Request(url=self.uri, headers=self.headers)
            try:
                with _JWKS_OPENER.open(request, timeout=self.timeout) as response:
                    jwk_set = json.load(response)
            except (urllib.error.URLError, TimeoutError) as exc:
                # Close the 3xx/error response stream (as PyJWKClient does) so a
                # refused or failed fetch does not leak the file descriptor.
                if isinstance(exc, urllib.error.HTTPError):
                    exc.close()
                raise jwt.exceptions.PyJWKClientConnectionError(
                    f'Fail to fetch data from the url, err: "{exc}"'
                ) from exc
            if self.jwk_set_cache is not None:
                self.jwk_set_cache.put(jwk_set)
            return jwk_set

    _hardened_jwk_client_cls = _RedirectHardenedJWKClient
    return _hardened_jwk_client_cls


def _jwk_client(jwks_url: str):
    """The JWKS client the verifier fetches signing keys through -- redirect-hardened by
    default, isolated behind this seam so a test can inject a fake without reaching into
    PyJWT internals."""
    return _redirect_hardened_jwk_client_cls()(jwks_url)


def verify_bearer(token: str | None, *, expected_nonce: str | None = None) -> Principal | None:
    """A verified `Principal` from an OIDC bearer JWT, or `None` on ANY failure.

    Fail-closed at every step: an absent token, an unconfigured verifier (issuer /
    audience / JWKS URL), an unfetchable or non-matching signing key, a bad
    signature, a wrong issuer or audience, an expired token, or a claimless token all
    return `None`. A successfully verified caller's ROLES are derived from the
    configured roles claim (`_roles_from_claims`, hy-09hy): the token's own role names
    when a claim is configured, the baseline `reader` when it is not. Authentication
    only -- `authz.authorize` is the fail-closed decision over these roles.

    When `expected_nonce` is given (the browser login callback, hy-jyha), the token's
    `nonce` claim MUST equal it -- this is the OIDC replay binding that ties a verified
    ID token back to the specific `/login` request that generated the nonce (stored in
    the signed login cookie). A missing or mismatched nonce denies. Bearer-token callers
    (API clients) pass no nonce and are not nonce-checked: a machine bearer has no login
    transaction to bind to.
    """
    if not token:
        return None
    issuer = _cfg(ISSUER_ENV)
    audience = _cfg(AUDIENCE_ENV)
    jwks_url = _cfg(JWKS_ENV)
    if not (issuer and audience and jwks_url):
        return None
    # HTTPS-ONLY, fail-closed on both the JWKS URL and the issuer -- Brandon's named
    # enable-precondition for ADR-0030 (overseer hq-rqq6, 2026-08-15). The JWKS URL is
    # the TRUST ANCHOR: it is fetched to get the signing keys the whole verification
    # rests on, and fetching it over cleartext `http://` lets a network attacker
    # substitute their own keys (MITM) and mint accepted tokens. The issuer is held to
    # the same bar: an OIDC issuer is an `https` URL that also anchors provider metadata
    # / discovery, so a non-HTTPS issuer is a misconfiguration we refuse rather than
    # trust. Both checks are before any key fetch, so a bad config issues no principal.
    # (Cost, recorded: a non-URL issuer such as a `urn:` is unsupported this cut -- a
    # deliberate fail-closed narrowing of "provider-neutral" to HTTPS OIDC issuers.)
    if not (jwks_url.lower().startswith("https://") and issuer.lower().startswith("https://")):
        return None
    try:
        import jwt

        signing_key = _jwk_client(jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer,
            # Require `exp`: a token minted with no expiry would otherwise verify and
            # never age out. `algorithms` is a strict allowlist, so `alg: none` and an
            # HS256 alg-confusion attempt (signing with the public key as an HMAC
            # secret) are both rejected before the signature is even checked.
            options={"require": ["exp"]},
        )
    except Exception:
        # Any verification or fetch failure denies -- never fall back to an
        # unauthenticated identity (the secret_box / github_app fail-closed posture).
        return None
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        return None
    if expected_nonce is not None:
        # OIDC ID-token replay binding: the nonce claim must match the one minted at
        # /login. A missing or mismatched nonce fails closed (constant-time compare).
        token_nonce = claims.get("nonce")
        if not isinstance(token_nonce, str) or not hmac.compare_digest(token_nonce, expected_nonce):
            return None
    workspace = _workspace_from_claims(claims)
    if workspace is None:
        return None
    return Principal(
        subject=subject,
        issuer=str(claims.get("iss") or issuer),
        roles=_roles_from_claims(claims),
        workspace=workspace,
    )


def principal_from_bearer(authorization: str | None) -> Principal | None:
    """The verified caller behind an HTTP `Authorization` header, or `None`. Returns
    `None` immediately when the gate is disabled (no verification work off the flag),
    and requires the `Bearer ` scheme. A transport calls this at its boundary and
    passes the result into `run_operation`; it never constructs a `Principal` itself."""
    if not authz_enabled():
        return None
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return verify_bearer(parts[1].strip())


class TokenExchangeError(RuntimeError):
    """The authorization-code -> token exchange failed. Fatal for the login attempt; the
    callback route turns it into a denied login, never a trusted identity."""


def exchange_code_for_id_token(
    *,
    code: str,
    code_verifier: str,
    token_endpoint: str,
    client_id: str,
    redirect_uri: str,
    client_secret: str | None = None,
    opener=None,
    timeout: int = 8,
) -> str:
    """POST an authorization code to the OIDC token endpoint (Authorization Code + PKCE) and
    return the raw `id_token` string, or raise `TokenExchangeError`. Fail-closed and
    HTTPS-only: the endpoint must be `https` (a token exchange over cleartext would leak the
    code and any client secret), and the request runs through the same redirect-hardened,
    cert-validating opener as the JWKS fetch, so a redirect off https is refused. The secret
    (if any) and the code go in the POST BODY, never a URL; nothing here is logged. The
    caller VERIFIES the returned id_token with `verify_bearer` -- this only obtains it."""
    if not (code and code_verifier and client_id and redirect_uri):
        raise TokenExchangeError("code, code_verifier, client_id, and redirect_uri are required")
    if not (token_endpoint and token_endpoint.lower().startswith("https://")):
        raise TokenExchangeError("the OIDC token endpoint must be https")
    fields = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }
    if client_secret:
        fields["client_secret"] = client_secret
    body = urllib.parse.urlencode(fields).encode("ascii")
    request = urllib.request.Request(
        token_endpoint,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    used = opener if opener is not None else _JWKS_OPENER
    try:
        with used.open(request, timeout=timeout) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        # Never surface the endpoint response body (it can echo the code/secret); name the
        # failure CLASS only.
        raise TokenExchangeError(f"token exchange failed ({type(exc).__name__})") from exc
    if not isinstance(payload, dict):
        raise TokenExchangeError("the token endpoint did not return a JSON object")
    id_token = payload.get("id_token")
    if not isinstance(id_token, str) or not id_token:
        raise TokenExchangeError("the token response carried no id_token")
    return id_token
