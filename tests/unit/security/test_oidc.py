"""The fail-closed OIDC bearer verifier (hy-ac2x, ADR-0030). Real RS256 tokens
signed by a test key, with the JWKS lookup mocked so no network is touched: a valid
token yields a reader `Principal`, and EVERY failure mode -- absent token, bad
signature, wrong issuer or audience, expiry, missing config, no subject -- yields
`None`, so the gate denies. Provider-neutral: the issuer is whatever the config names.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from hyperset.security import oidc

ISSUER = "https://issuer.example/"
AUDIENCE = "hyperset"
JWKS_URL = "https://issuer.example/.well-known/jwks.json"


def _pem_pair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


PRIVATE_PEM, PUBLIC_PEM = _pem_pair()
OTHER_PRIVATE_PEM, _ = _pem_pair()


def _token(private_pem=PRIVATE_PEM, **overrides) -> str:
    now = datetime.now(tz=UTC)
    claims = {
        "sub": "user-123",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode(claims, private_pem, algorithm="RS256")


@pytest.fixture
def configured(monkeypatch):
    """The verifier configured, with the JWKS fetch mocked to return the test public
    key -- so `verify_bearer` runs the real `jwt.decode` against a real signature but
    reaches no network."""
    monkeypatch.setenv(oidc.ISSUER_ENV, ISSUER)
    monkeypatch.setenv(oidc.AUDIENCE_ENV, AUDIENCE)
    monkeypatch.setenv(oidc.JWKS_ENV, JWKS_URL)

    class _FakeJWKClient:
        def __init__(self, url):
            self.url = url

        def get_signing_key_from_jwt(self, token):
            return SimpleNamespace(key=PUBLIC_PEM)

    # Inject at the `_jwk_client` seam -- the real client is redirect-hardened; a unit
    # test injects a fake without reaching PyJWT internals.
    monkeypatch.setattr(oidc, "_jwk_client", _FakeJWKClient)


def test_a_valid_token_yields_a_reader_principal(configured):
    principal = oidc.verify_bearer(_token())
    assert principal is not None
    assert principal.subject == "user-123"
    assert principal.issuer == ISSUER
    assert principal.roles == ("reader",)


def test_a_matching_expected_nonce_is_accepted(configured):
    # The browser login callback (hy-jyha) passes the nonce it minted at /login; a token
    # whose `nonce` claim matches binds to that login and verifies.
    principal = oidc.verify_bearer(_token(nonce="n-abc"), expected_nonce="n-abc")
    assert principal is not None and principal.subject == "user-123"


def test_a_mismatched_expected_nonce_is_rejected(configured):
    # OIDC replay binding: a token whose nonce does not match the login's nonce denies,
    # even though it is otherwise a valid, correctly-signed token for this audience/issuer.
    assert oidc.verify_bearer(_token(nonce="n-other"), expected_nonce="n-abc") is None


def test_a_token_with_no_nonce_is_rejected_when_a_nonce_is_expected(configured):
    # Fail closed: if the callback expects a nonce but the ID token carries none, deny --
    # a token minted without the login's nonce cannot be bound to this login.
    assert oidc.verify_bearer(_token(), expected_nonce="n-abc") is None


def test_a_bearer_without_an_expected_nonce_ignores_the_nonce_claim(configured):
    # A machine bearer (API client) has no login transaction and passes no expected_nonce;
    # a nonce claim on the token, if any, is not checked -- the default path is unchanged.
    assert oidc.verify_bearer(_token(nonce="stray")) is not None
    assert oidc.verify_bearer(_token()) is not None


def test_a_token_signed_by_another_key_is_rejected(configured):
    # The JWKS returns our public key; a token signed by a different private key
    # fails the signature check and denies.
    assert oidc.verify_bearer(_token(private_pem=OTHER_PRIVATE_PEM)) is None


def test_a_wrong_audience_is_rejected(configured):
    assert oidc.verify_bearer(_token(aud="someone-else")) is None


def test_a_wrong_issuer_is_rejected(configured):
    assert oidc.verify_bearer(_token(iss="https://evil.example/")) is None


def test_an_expired_token_is_rejected(configured):
    past = datetime.now(tz=UTC) - timedelta(hours=1)
    assert oidc.verify_bearer(_token(iat=past, exp=past + timedelta(minutes=1))) is None


def test_a_token_with_no_subject_is_rejected(configured):
    assert oidc.verify_bearer(_token(sub="")) is None


def test_an_absent_token_is_rejected(configured):
    assert oidc.verify_bearer(None) is None
    assert oidc.verify_bearer("") is None


def test_a_token_with_no_expiry_is_rejected(configured):
    # A non-expiring token (no `exp` claim) must not verify -- otherwise a leaked
    # token would be valid forever. `exp` is required, not merely checked-if-present.
    now = datetime.now(tz=UTC)
    no_exp = jwt.encode(
        {"sub": "user-123", "iss": ISSUER, "aud": AUDIENCE, "iat": now},
        PRIVATE_PEM,
        algorithm="RS256",
    )
    assert oidc.verify_bearer(no_exp) is None


def test_an_alg_none_token_is_rejected(configured):
    # `alg: none` (unsigned) must be rejected by the RS256 allowlist.
    now = datetime.now(tz=UTC)
    unsigned = jwt.encode(
        {"sub": "u", "iss": ISSUER, "aud": AUDIENCE, "exp": now + timedelta(minutes=5)},
        key=None,
        algorithm="none",
    )
    assert oidc.verify_bearer(unsigned) is None


def test_a_non_allowlisted_rsa_alg_is_rejected(configured):
    # The RS256 pin, ISOLATED. A VALID RS384 signature by the real signing key -- same
    # RSA key, correct aud/iss/exp -- is rejected ONLY because RS384 is not on the
    # allowlist: widening to `algorithms=["RS256","RS384"]` would verify it. This is
    # what an HS256 confusion token CANNOT prove: PyJWT itself refuses a PEM as an HMAC
    # key, so an HS256 forgery is rejected by PyJWT regardless of our allowlist, and a
    # test over it stays green even when the allowlist is widened.
    now = datetime.now(tz=UTC)
    rs384 = jwt.encode(
        {"sub": "u", "iss": ISSUER, "aud": AUDIENCE, "exp": now + timedelta(minutes=5)},
        PRIVATE_PEM,
        algorithm="RS384",
    )
    assert oidc.verify_bearer(rs384) is None


def test_an_hs256_token_is_rejected(configured):
    # Defense-in-depth outcome (not an allowlist-isolating check): an HS256 token does
    # not verify. Both the RS256 allowlist AND PyJWT's refusal of a PEM as an HMAC key
    # reject it.
    now = datetime.now(tz=UTC)
    forged = jwt.encode(
        {"sub": "u", "iss": ISSUER, "aud": AUDIENCE, "exp": now + timedelta(minutes=5)},
        "attacker-chosen-secret-of-ample-length-32b",
        algorithm="HS256",
    )
    assert oidc.verify_bearer(forged) is None


def test_a_valid_token_with_the_verifier_unconfigured_is_rejected(monkeypatch):
    # Fail closed on misconfiguration: even a well-formed token denies when the
    # issuer/audience/JWKS URL are not all set.
    monkeypatch.delenv(oidc.ISSUER_ENV, raising=False)
    monkeypatch.setenv(oidc.AUDIENCE_ENV, AUDIENCE)
    monkeypatch.setenv(oidc.JWKS_ENV, JWKS_URL)
    assert oidc.verify_bearer(_token()) is None


def test_a_cleartext_http_jwks_url_is_rejected_without_fetching(monkeypatch):
    # The JWKS URL is the trust anchor; an `http://` one lets an attacker MITM the
    # signing keys. The fake here SUCCEEDS (returns the real key), so the ONLY thing
    # that can produce `None` is the HTTPS check -- and it must reject BEFORE the
    # client is even constructed, proven by the empty `constructed` list. If the check
    # were removed, PyJWKClient would be built, the good key returned, and the token
    # would verify to a Principal -- so this fails closed only because of the check.
    monkeypatch.setenv(oidc.ISSUER_ENV, ISSUER)
    monkeypatch.setenv(oidc.AUDIENCE_ENV, AUDIENCE)
    monkeypatch.setenv(oidc.JWKS_ENV, "http://issuer.example/.well-known/jwks.json")

    constructed: list[str] = []

    class _WorkingJWKClient:
        def __init__(self, url):
            constructed.append(url)

        def get_signing_key_from_jwt(self, token):
            return SimpleNamespace(key=PUBLIC_PEM)

    monkeypatch.setattr(oidc, "_jwk_client", _WorkingJWKClient)
    assert oidc.verify_bearer(_token()) is None
    assert constructed == []


def test_a_cleartext_http_issuer_is_rejected_without_fetching(monkeypatch):
    # The issuer is held to the same HTTPS bar as the JWKS URL (Brandon hq-rqq6). The
    # fake succeeds, so only the issuer HTTPS check can produce `None`, and it rejects
    # before the client is constructed (`constructed == []`). Removing the issuer arm
    # of the check would build the client, return the good key, and verify a Principal.
    monkeypatch.setenv(oidc.ISSUER_ENV, "http://issuer.example/")
    monkeypatch.setenv(oidc.AUDIENCE_ENV, AUDIENCE)
    monkeypatch.setenv(oidc.JWKS_ENV, JWKS_URL)

    constructed: list[str] = []

    class _WorkingJWKClient:
        def __init__(self, url):
            constructed.append(url)

        def get_signing_key_from_jwt(self, token):
            return SimpleNamespace(key=PUBLIC_PEM)

    monkeypatch.setattr(oidc, "_jwk_client", _WorkingJWKClient)
    # Sign with the http issuer so the token's `iss` matches config -- the only barrier
    # is the HTTPS check, not an issuer mismatch.
    assert oidc.verify_bearer(_token(iss="http://issuer.example/")) is None
    assert constructed == []


def test_a_jwks_fetch_failure_is_rejected(monkeypatch):
    monkeypatch.setenv(oidc.ISSUER_ENV, ISSUER)
    monkeypatch.setenv(oidc.AUDIENCE_ENV, AUDIENCE)
    monkeypatch.setenv(oidc.JWKS_ENV, JWKS_URL)

    class _Exploding:
        def __init__(self, url):
            pass

        def get_signing_key_from_jwt(self, token):
            raise RuntimeError("jwks endpoint unreachable")

    monkeypatch.setattr(oidc, "_jwk_client", _Exploding)
    assert oidc.verify_bearer(_token()) is None


# --- authz_enabled + principal_from_bearer ---


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " on "])
def test_the_flag_reads_truthy_values(monkeypatch, value):
    monkeypatch.setenv(oidc.ENABLED_ENV, value)
    assert oidc.authz_enabled() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "nope"])
def test_the_flag_defaults_and_reads_falsey_values(monkeypatch, value):
    monkeypatch.setenv(oidc.ENABLED_ENV, value)
    assert oidc.authz_enabled() is False


def test_the_flag_is_disabled_when_unset(monkeypatch):
    monkeypatch.delenv(oidc.ENABLED_ENV, raising=False)
    assert oidc.authz_enabled() is False


def test_principal_from_bearer_returns_none_when_the_gate_is_disabled(configured, monkeypatch):
    # Even a valid bearer verifies to nothing when the gate is off: no verification
    # work happens, and the gate at run_operation is a no-op regardless.
    monkeypatch.delenv(oidc.ENABLED_ENV, raising=False)
    assert oidc.principal_from_bearer(f"Bearer {_token()}") is None


def test_principal_from_bearer_verifies_a_bearer_header_when_enabled(configured, monkeypatch):
    monkeypatch.setenv(oidc.ENABLED_ENV, "1")
    principal = oidc.principal_from_bearer(f"Bearer {_token()}")
    assert principal is not None and principal.subject == "user-123"


@pytest.mark.parametrize(
    "header", [None, "", "Basic abc", "Bearer", "token-without-scheme", "Bearer  "]
)
def test_principal_from_bearer_rejects_a_non_bearer_or_empty_header(
    configured, monkeypatch, header
):
    monkeypatch.setenv(oidc.ENABLED_ENV, "1")
    assert oidc.principal_from_bearer(header) is None


# --- hy-3xs0: a JWKS redirect off https is refused (enable-precondition #2) ---


def test_the_redirect_handler_refuses_a_downgrade_but_allows_https():
    import email.message
    import io

    handler = oidc._HTTPSOnlyRedirectHandler()
    req = urllib.request.Request("https://issuer.example/.well-known/jwks.json")
    msg = email.message.Message()

    # A redirect to http:// (or any non-https scheme) is refused hard.
    with pytest.raises(urllib.error.HTTPError):
        handler.redirect_request(
            req, io.BytesIO(b""), 302, "Found", msg, "http://evil.example/jwks"
        )

    # A legitimate https -> https redirect is still followed.
    allowed = handler.redirect_request(
        req, io.BytesIO(b""), 302, "Found", msg, "https://issuer.example/keys"
    )
    assert allowed is not None
    assert allowed.get_full_url() == "https://issuer.example/keys"


def test_a_jwks_url_that_redirects_to_http_never_fetches_the_keys():
    """The MITM re-entry the initial https check misses: an https (here http, to keep the
    test serverless of TLS) JWKS URL that 3xx-redirects to an http:// location. The fetch
    must REFUSE the downgrade and never contact the http target, so no substituted key is
    trusted. Proven by (a) the fetch raising with the refusal message -- not a mere
    connection error -- and (b) the http target recording zero requests."""
    import http.server
    import threading

    target_hits: list[str] = []

    class _Target(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # pragma: no cover - must NEVER run
            target_hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"keys": []}')

        def log_message(self, *args):
            pass

    class _Redirector(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{target_port}/jwks")
            self.end_headers()

        def log_message(self, *args):
            pass

    target = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Target)
    target_port = target.server_address[1]
    redirector = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Redirector)
    redirect_port = redirector.server_address[1]
    for server in (target, redirector):
        threading.Thread(target=server.serve_forever, daemon=True).start()

    try:
        client = oidc._jwk_client(f"http://127.0.0.1:{redirect_port}/jwks")
        with pytest.raises(Exception) as caught:
            client.fetch_data()
        # The refusal fired (my handler), not a coincidental connection failure.
        assert "refused a JWKS redirect to a non-HTTPS URL" in str(caught.value)
        # The cleartext target was never contacted.
        assert target_hits == []
    finally:
        for server in (target, redirector):
            server.shutdown()
            server.server_close()


# --- hy-09hy (F2): Principal.roles derived from the configured roles claim ---


def _roles_env(monkeypatch, claim="roles"):
    monkeypatch.setenv(oidc.ROLES_CLAIM_ENV, claim)


def test_roles_come_from_the_configured_claim_as_a_list(configured, monkeypatch):
    _roles_env(monkeypatch)
    principal = oidc.verify_bearer(_token(roles=["reviewer", "admin"]))
    assert principal is not None
    assert principal.roles == ("reviewer", "admin")


def test_roles_come_from_a_delimited_string_claim(configured, monkeypatch):
    # A single space/comma-delimited string is a common IdP shape; both split, empties
    # drop, and order de-dupes.
    _roles_env(monkeypatch)
    principal = oidc.verify_bearer(_token(roles="reviewer, reviewer  admin"))
    assert principal is not None
    assert principal.roles == ("reviewer", "admin")


def test_no_configured_claim_keeps_the_baseline_reader_role(configured, monkeypatch):
    # Opt-out path: with no HYPERSET_OIDC_ROLES_CLAIM set, a verified caller is a
    # reader exactly as before this slice -- byte-identical for a deployment that has
    # not configured role mapping. `configured` does not set the claim env.
    monkeypatch.delenv(oidc.ROLES_CLAIM_ENV, raising=False)
    principal = oidc.verify_bearer(_token(roles=["admin"]))
    assert principal is not None
    assert principal.roles == ("reader",)


def test_a_configured_claim_absent_from_the_token_yields_no_roles(configured, monkeypatch):
    # Least privilege, fail-closed: the claim is configured but the token carries no
    # roles claim, so the caller has NO roles. Reader is NOT a silent fallback here.
    _roles_env(monkeypatch)
    principal = oidc.verify_bearer(_token())
    assert principal is not None
    assert principal.roles == ()


def test_a_non_string_or_array_roles_claim_yields_no_roles(configured, monkeypatch):
    _roles_env(monkeypatch)
    principal = oidc.verify_bearer(_token(roles=42))
    assert principal is not None
    assert principal.roles == ()


def test_derived_roles_are_authorized_fail_closed(configured, monkeypatch):
    # The end-to-end point: a derived role name is only authority if the registry
    # knows it. `reader` from the claim ALLOWS a read; an UNKNOWN role name grants
    # nothing, so authorize DENIES. `reviewer` is a REAL role now (hy-dq0r landed the
    # vocabulary), so the "unknown" stand-in must be a name the registry does not
    # define -- otherwise this would assert a known role is denied and rot into the
    # opposite of its intent. It also proves the mapped role is the one the gate reads,
    # not a hardcoded reader.
    from hyperset.security.authz import ROLES, Resource, authorize

    _roles_env(monkeypatch)
    resource = Resource(domain="revenue")

    reader = oidc.verify_bearer(_token(roles=["reader"]))
    assert reader.roles == ("reader",)
    assert authorize(reader, "read", resource, ROLES).allowed is True

    assert "made_up_role" not in ROLES
    unknown = oidc.verify_bearer(_token(roles=["made_up_role"]))
    assert unknown.roles == ("made_up_role",)
    assert authorize(unknown, "read", resource, ROLES).allowed is False

    # No roles at all (configured claim, none in token) is likewise denied.
    none_mapped = oidc.verify_bearer(_token())
    assert none_mapped.roles == ()
    assert authorize(none_mapped, "read", resource, ROLES).allowed is False


def test_a_genuine_client_credentials_token_is_accepted_as_a_service_principal(
    configured, monkeypatch
):
    # F3 (hq-l4g2 rec a): a NON-HUMAN service authenticates via OIDC client-credentials.
    # The DEFINING shape of such a token is `sub == client_id` (RFC 9068) -- no distinct
    # end user, the subject IS the client -- and only that shape may carry `service`
    # (hy-okm6). Verified by the SAME path as a human token, yielding a machine Principal.
    _roles_env(monkeypatch)
    principal = oidc.verify_bearer(
        _token(sub="svc-analytics", client_id="svc-analytics", roles=["service"])
    )
    assert principal is not None
    assert principal.subject == "svc-analytics"
    assert principal.roles == ("service",)


def test_a_service_token_identified_by_azp_equal_to_sub_is_accepted(configured, monkeypatch):
    # The OIDC `azp` (authorized party) claim is the alternate client-id carrier: a
    # client-credentials token whose `azp` equals `sub` is likewise a genuine service.
    _roles_env(monkeypatch)
    principal = oidc.verify_bearer(_token(sub="svc-etl", azp="svc-etl", roles=["service"]))
    assert principal is not None
    assert principal.roles == ("service",)


def test_a_human_bearer_listing_service_is_denied_the_service_role(configured, monkeypatch):
    # THE BOUNCE (hy-okm6, adversary @ d01d218): a verified HUMAN token -- sub is the user,
    # the client id is a different app (or absent) -- that merely lists `roles=["service"]`
    # must NOT become a service identity. `service` is stripped; the human keeps nothing it
    # is not entitled to.
    from hyperset.security.authz import ROLES, Resource, authorize

    _roles_env(monkeypatch)
    # A human authorization-code token: subject is the user, client id is a distinct app.
    human = oidc.verify_bearer(_token(sub="user-123", client_id="webapp-client", roles=["service"]))
    assert human is not None
    assert "service" not in human.roles
    assert human.roles == ()
    # And it authorizes to nothing (least privilege): service scope is not inherited.
    assert authorize(human, "read", Resource(domain="revenue"), ROLES).allowed is False


def test_a_human_bearer_listing_service_and_reviewer_keeps_reviewer_but_not_service(
    configured, monkeypatch
):
    # `roles=["service","reviewer"]` on a human token must drop `service` (machine-only)
    # while keeping the legitimate human `reviewer` role -- and the caller must NOT gain
    # REVIEW *via* a forged service identity. reviewer's OWN review grant is unaffected;
    # the point is that stripping `service` is surgical, not a blanket denial.
    from hyperset.security.authz import REVIEW, ROLES, Resource, authorize

    _roles_env(monkeypatch)
    human = oidc.verify_bearer(
        _token(sub="user-123", client_id="webapp-client", roles=["service", "reviewer"])
    )
    assert human is not None
    assert "service" not in human.roles
    assert human.roles == ("reviewer",)
    # reviewer is a real human role, so REVIEW is allowed -- but through reviewer, never
    # through the stripped service identity.
    assert authorize(human, REVIEW, Resource(domain="revenue"), ROLES).allowed is True


def test_a_bearer_with_no_client_id_claim_cannot_assert_service(configured, monkeypatch):
    # Fail closed: absent positive proof of client-credentials (no client_id/azp at all),
    # `service` is denied even when nothing contradicts it.
    _roles_env(monkeypatch)
    principal = oidc.verify_bearer(_token(sub="svc-analytics", roles=["service"]))
    assert principal is not None
    assert principal.roles == ()


# --- the authorization-code -> token exchange (F4 route-wiring, hy-jyha) ---


class _FakeTokenResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeOpener:
    def __init__(self, *, payload=None, exc=None):
        self.payload = payload
        self.exc = exc
        self.last_request = None

    def open(self, request, timeout=None):
        self.last_request = request
        if self.exc is not None:
            raise self.exc
        return _FakeTokenResponse(self.payload)


def test_exchange_returns_the_id_token_and_posts_the_pkce_verifier_and_secret_in_the_body():
    opener = _FakeOpener(payload={"id_token": "the.id.token", "access_token": "at"})
    token = oidc.exchange_code_for_id_token(
        code="authcode",
        code_verifier="verifier123",
        token_endpoint="https://idp.example/token",
        client_id="cid",
        redirect_uri="https://app.example/cb",
        client_secret="s3cret",
        opener=opener,
    )
    assert token == "the.id.token"
    body = opener.last_request.data.decode()
    assert "grant_type=authorization_code" in body and "code=authcode" in body
    assert "code_verifier=verifier123" in body and "client_secret=s3cret" in body
    # The token endpoint is a POST; the code/secret are in the BODY, never the URL.
    assert opener.last_request.get_method() == "POST"
    assert "authcode" not in opener.last_request.full_url


def test_exchange_omits_the_secret_for_a_public_pkce_client():
    opener = _FakeOpener(payload={"id_token": "t"})
    oidc.exchange_code_for_id_token(
        code="c",
        code_verifier="v",
        token_endpoint="https://idp.example/token",
        client_id="cid",
        redirect_uri="https://app.example/cb",
        opener=opener,
    )
    assert "client_secret" not in opener.last_request.data.decode()


def test_exchange_refuses_a_non_https_token_endpoint():
    with pytest.raises(oidc.TokenExchangeError):
        oidc.exchange_code_for_id_token(
            code="c",
            code_verifier="v",
            token_endpoint="http://idp.example/token",
            client_id="cid",
            redirect_uri="https://app.example/cb",
            opener=_FakeOpener(payload={}),
        )


def test_exchange_fails_closed_on_network_error_and_a_missing_id_token():
    down = _FakeOpener(exc=urllib.error.URLError("connection refused"))
    with pytest.raises(oidc.TokenExchangeError):
        oidc.exchange_code_for_id_token(
            code="c",
            code_verifier="v",
            token_endpoint="https://idp.example/token",
            client_id="cid",
            redirect_uri="https://app.example/cb",
            opener=down,
        )
    no_id = _FakeOpener(payload={"access_token": "at"})  # a token response with no id_token
    with pytest.raises(oidc.TokenExchangeError):
        oidc.exchange_code_for_id_token(
            code="c",
            code_verifier="v",
            token_endpoint="https://idp.example/token",
            client_id="cid",
            redirect_uri="https://app.example/cb",
            opener=no_id,
        )


# --- hq-t6nx (ADR-0037): Principal.workspace derived from the configured claim ---


def test_workspace_comes_from_the_configured_claim(configured, monkeypatch):
    monkeypatch.setenv(oidc.WORKSPACE_CLAIM_ENV, "tenant")
    principal = oidc.verify_bearer(_token(tenant="acme"))
    assert principal is not None
    assert principal.workspace == "acme"


def test_no_configured_workspace_claim_is_the_default_workspace(configured, monkeypatch):
    # Opt-out path: with no HYPERSET_OIDC_WORKSPACE_CLAIM set, every caller acts in
    # the single implicit 'default' workspace -- byte-identical to before this slice.
    monkeypatch.delenv(oidc.WORKSPACE_CLAIM_ENV, raising=False)
    principal = oidc.verify_bearer(_token(tenant="acme"))
    assert principal is not None
    assert principal.workspace == "default"


def test_a_missing_or_blank_workspace_claim_fails_closed_to_denial(configured, monkeypatch):
    # Once a workspace claim is configured, a token that omits it (or leaves it
    # blank / non-string) is denied rather than silently entering 'default'.
    monkeypatch.setenv(oidc.WORKSPACE_CLAIM_ENV, "tenant")
    assert oidc.verify_bearer(_token()) is None
    assert oidc.verify_bearer(_token(tenant="   ")) is None
    assert oidc.verify_bearer(_token(tenant=["acme"])) is None
