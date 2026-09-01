"""The one authorization decision point at `run_operation`, enforced (hy-ac2x,
ADR-0030). These exercise the GATE -- the flag, the fail-closed denial, deny-the-whole,
and the non-disclosing uniformity -- over the shared executor both transports call.
The pure decision is covered in `tests/unit/authz/test_authz_model.py`. The live
OIDC/JWT bearer verifier is a deferred cut (hy-lrho); here the principal is handed in
directly (the in-process `SYSTEM_PRINCIPAL` shape, or a constructed reader), which is
exactly what the executor supplies today.
"""

from __future__ import annotations

import pytest

from hyperset.security.authz import Principal
from hyperset.transport.operations import _DENIAL_MESSAGE as DENIAL_MESSAGE
from hyperset.transport.operations import (
    UNAUTHORIZED,
    OperationError,
    run_operation,
)
from tests.unit.transport.conftest import DIRECTIVE, QUESTION, governed_bundle

RESOLVE = "resolve_analytics_context"
CATALOG = "list_context_catalog"
_ABSENT = {"domains": ["no_such_domain"], "concepts": ["nothing"]}


def _reader() -> Principal:
    return Principal(subject="u1", issuer="https://issuer.example", roles=("reader",))


def _resolve(session_factory, principal, *, directive=DIRECTIVE):
    return run_operation(
        RESOLVE,
        {"query": QUESTION, "directive": directive},
        session_factory=session_factory,
        principal=principal,
    )


# --- Disabled by default: byte-identical to today, whatever the principal ---


def test_with_the_gate_off_the_resolve_path_is_untouched(resolved, session_factory):
    """The rollout-safety invariant: `HYPERSET_AUTHZ_ENABLED` unset (the default)
    leaves `run_operation` exactly as it was. No principal, and a principal that
    WOULD be denied were the gate on, both serve the same bundle today's callers get
    -- so an existing dev/demo/CI caller is not broken by this code being present."""
    baseline = governed_bundle().to_dict()
    assert _resolve(session_factory, None) == baseline
    assert _resolve(session_factory, _reader()) == baseline
    # A principal that an ENABLED gate denies (no roles) is served unchanged when off.
    assert _resolve(session_factory, Principal(subject="x", issuer="i", roles=())) == baseline
    # The resolver actually ran each time -- the path was exercised, not short-circuited.
    assert len(resolved) == 3


# --- Enabled: fail-closed authN boundary, deny-the-whole ---


def test_enabled_with_no_identity_is_a_complete_denial_and_never_dispatches(
    resolved, session_factory, monkeypatch
):
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    with pytest.raises(OperationError) as caught:
        _resolve(session_factory, None)
    assert caught.value.code == UNAUTHORIZED
    # DENY-THE-WHOLE: the gate is before dispatch, so the resolver was never called --
    # no bundle, partial, or provenance was assembled to then be stripped.
    assert resolved == []


def test_enabled_a_verified_reader_reads_the_governed_bundle(
    resolved, session_factory, monkeypatch
):
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    assert _resolve(session_factory, _reader()) == governed_bundle().to_dict()
    assert len(resolved) == 1


def test_enabled_an_unknown_role_is_denied_at_the_gate(resolved, session_factory, monkeypatch):
    from hyperset.security import authz

    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    # A name the registry does not know. `admin` is now a REAL role (hy-dq0r), so the
    # stand-in for "unknown" must be a name nothing defines, or the test would assert a
    # known role is denied and rot into the opposite of its intent.
    assert "made_up_role" not in authz.ROLES
    with pytest.raises(OperationError) as caught:
        _resolve(session_factory, Principal(subject="u", issuer="i", roles=("made_up_role",)))
    assert caught.value.code == UNAUTHORIZED
    assert resolved == []


# --- Non-disclosure: existing and absent resources denied identically ---


def test_the_denial_is_byte_identical_for_an_existing_and_an_absent_resource(
    resolved, session_factory, monkeypatch
):
    """No existence signal: an unauthorized caller gets the SAME error whether it
    named a resource that exists or one that does not. The gate runs before dispatch
    and the message names no resource, so the two denials are indistinguishable."""
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    with pytest.raises(OperationError) as existing:
        _resolve(session_factory, None, directive=DIRECTIVE)
    with pytest.raises(OperationError) as absent:
        _resolve(session_factory, None, directive=_ABSENT)
    assert existing.value.to_dict() == absent.value.to_dict()
    # Pin the FIXED message, not just equality between the two: equality alone passes
    # even a message that interpolates the resource (both would interpolate the same
    # empty domain). Requiring the constant catches ANY interpolation -- a
    # `f"not authorized for {domain}"` mutation yields "not authorized for None",
    # which is not the constant.
    assert existing.value.message == DENIAL_MESSAGE
    assert resolved == []


def test_every_operation_is_gated_not_only_resolve(listed, session_factory, monkeypatch):
    """The gate is the ONE decision point for the whole executor: an unauthenticated
    caller is denied the catalog too, identically, and the catalog service never runs."""
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    with pytest.raises(OperationError) as caught:
        run_operation(CATALOG, {}, session_factory=session_factory, principal=None)
    assert caught.value.code == UNAUTHORIZED
    assert listed == []


# --- FIX 2 (bounce): the gate binds to the REAL directive domains, not a false '' ---

from hyperset.security import authz as _authz  # noqa: E402
from hyperset.security.authz import READER, Effect, Grant, Role, Scope  # noqa: E402
from hyperset.transport import operations as _ops  # noqa: E402

_DENY_REVENUE = Role(
    name="dr",
    grants=(
        Grant(Effect.ALLOW, "read", Scope()),  # reads all governed context ...
        Grant(Effect.DENY, "read", Scope(domain="revenue")),  # ... except revenue (deny wins)
    ),
)
_ONLY_REVENUE = Role(name="or", grants=(Grant(Effect.ALLOW, "read", Scope(domain="revenue")),))
# Allows exactly the one-character domain "r": the case a bare-string `domains` of
# "r" would COINCIDENTALLY satisfy if the gate iterated the string into characters.
_ONLY_R = Role(name="onlyr", grants=(Grant(Effect.ALLOW, "read", Scope(domain="r")),))
_SCOPED_ROLES = {"reader": READER, "dr": _DENY_REVENUE, "or": _ONLY_REVENUE, "onlyr": _ONLY_R}
_FINANCE = {"domains": ["finance"], "concepts": ["x"]}
_TWO_DOMAINS = {"domains": ["revenue", "finance"], "concepts": ["x"]}


def _principal(role: str) -> Principal:
    return Principal(subject="u", issuer="i", roles=(role,))


def test_a_per_domain_deny_binds_to_the_real_directive_domain(
    resolved, session_factory, monkeypatch
):
    """The bounce defect: the gate authorized `Resource(domain="")` regardless of the
    request, so a DENY on `revenue` never fired. Now the domain comes from
    `directive["domains"]`: a reader denied on `revenue` cannot resolve `revenue`, but
    still reads a different domain. (Old code allowed the `revenue` resolve -- the
    deny scope `revenue` did not cover `""`.)"""
    monkeypatch.setattr(_authz, "ROLES", _SCOPED_ROLES)
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    with pytest.raises(OperationError) as caught:
        _resolve(session_factory, _principal("dr"), directive=DIRECTIVE)  # domains=["revenue"]
    assert caught.value.code == UNAUTHORIZED
    assert resolved == []
    # A domain the deny does not name still reads.
    assert (
        _resolve(session_factory, _principal("dr"), directive=_FINANCE)
        == governed_bundle().to_dict()
    )


def test_a_multi_domain_directive_is_denied_whole_if_any_domain_is_denied(
    resolved, session_factory, monkeypatch
):
    monkeypatch.setattr(_authz, "ROLES", _SCOPED_ROLES)
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    with pytest.raises(OperationError) as caught:
        _resolve(session_factory, _principal("dr"), directive=_TWO_DOMAINS)
    assert caught.value.code == UNAUTHORIZED
    # Deny-the-whole: the whole request is refused, the resolver never runs.
    assert resolved == []


def test_a_domain_scoped_reader_reads_its_domain(resolved, session_factory, monkeypatch):
    monkeypatch.setattr(_authz, "ROLES", _SCOPED_ROLES)
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    assert (
        _resolve(session_factory, _principal("or"), directive=DIRECTIVE)
        == governed_bundle().to_dict()
    )
    assert len(resolved) == 1


def test_an_unresolvable_domain_denies_a_domain_scoped_grant_never_defaults_to_all(
    listed, session_factory, monkeypatch
):
    """A cross-domain op with no per-domain resource (catalog) must NOT silently read
    under a domain-scoped grant. The sentinel domain no real slug equals means a
    `revenue`-scoped reader is DENIED the catalog, while the all-domain reader lists."""
    monkeypatch.setattr(_authz, "ROLES", _SCOPED_ROLES)
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    with pytest.raises(OperationError) as caught:
        run_operation(CATALOG, {}, session_factory=session_factory, principal=_principal("or"))
    assert caught.value.code == UNAUTHORIZED
    assert listed == []
    # The all-domain reader still lists.
    assert run_operation(CATALOG, {}, session_factory=session_factory, principal=_reader())
    assert len(listed) == 1


def test_expand_needs_an_all_domain_reader_grant(monkeypatch):
    """expand's start domain rides as a top-level `domain` param, not in a `directive`, so
    it resolves to the unresolvable sentinel: a domain-scoped reader is DENIED (fail-closed,
    the coarse slice-4 gate), and only the all-domain reader may navigate (#230 slice 4)."""
    monkeypatch.setattr(_authz, "ROLES", _SCOPED_ROLES)
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    params = {"query": "q", "domain": "revenue", "concepts": ["recognized_revenue"]}
    # A reader scoped to exactly `revenue` cannot expand FROM revenue: the gate never sees
    # the domain (it is not in a directive), so it fails closed to the all-domain grant.
    assert _ops.authorization_error(_ops.EXPAND, params, _principal("or")) is not None
    assert _ops.authorization_error(_ops.EXPAND, params, _reader()) is None


def test_a_non_list_domains_is_not_iterated_it_fails_closed(resolved, session_factory, monkeypatch):
    """A `directive["domains"]` that is a bare STRING is not a domains list and must
    not be iterated into per-character resources. "r" under a grant that allows the
    one-char domain "r" would be COINCIDENTALLY served if the gate iterated it; the
    isinstance(list) guard sends it to the unresolvable sentinel instead, which the
    "r"-scoped grant does not cover -- so it fails closed and denies."""
    monkeypatch.setattr(_authz, "ROLES", _SCOPED_ROLES)
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    with pytest.raises(OperationError) as caught:
        _resolve(
            session_factory, _principal("onlyr"), directive={"domains": "r", "concepts": ["x"]}
        )
    assert caught.value.code == UNAUTHORIZED
    assert resolved == []


# --- hy-dq0r: the role vocabulary + the READ/REVIEW action split at the gate ---


def test_a_reviewer_may_author_a_review_op_and_a_reader_may_not(monkeypatch):
    # The review-AUTHORING ops require the REVIEW action, so a read-only role is denied
    # while a reviewer (and the trusted in-process system identity) is allowed. The gate
    # decision is tested as a value -- no dispatch, no review-task machinery.
    from hyperset.security.authz import SYSTEM_PRINCIPAL
    from hyperset.transport.operations import EDIT_REVIEW_DRAFT, authorization_error

    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    reviewer = Principal("u", "i", roles=("reviewer",))
    params = {"task_id": "t1"}

    assert authorization_error(EDIT_REVIEW_DRAFT, params, reviewer) is None
    assert authorization_error(EDIT_REVIEW_DRAFT, params, SYSTEM_PRINCIPAL) is None
    for role in ("reader", "explorer", "admin", "git_owner"):
        denied = authorization_error(EDIT_REVIEW_DRAFT, params, Principal("u", "i", roles=(role,)))
        assert denied is not None and denied.code == UNAUTHORIZED


def test_every_named_role_reads_governed_context(monkeypatch):
    # Each posture role resolves to a read grant: an IdP token carrying `admin` or
    # `git_owner` must READ, not be denied as an unknown role (fail-closed the wrong way).
    from hyperset.transport.operations import authorization_error

    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    for role in ("reader", "explorer", "reviewer", "admin", "git_owner"):
        principal = Principal("u", "i", roles=(role,))
        params = {"query": QUESTION, "directive": DIRECTIVE}
        assert authorization_error(RESOLVE, params, principal) is None, role


def test_the_review_action_mapping_is_load_bearing(monkeypatch):
    # Guards OPERATION_ACTIONS itself: all three review-author ops require REVIEW, so a
    # reader is denied each. If the mapping were dropped they would default to READ and a
    # reader would author -- this reds on that regression.
    from hyperset.transport.operations import (
        EDIT_REVIEW_DRAFT,
        PROPOSE_REVIEW_TO_GIT,
        REFINE_REVIEW_DRAFT,
        REQUEST_REVIEW_EVIDENCE,
        SET_REVIEW_ASSIGNEE,
        authorization_error,
    )

    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    reader = Principal("u", "i", roles=("reader",))
    # REQUEST_REVIEW_EVIDENCE is an HTTP-only op (not in OPERATIONS), but it re-gathers a task's
    # evidence -- an authoring mutation -- so it maps to REVIEW too and a reader is denied it.
    for op in (
        EDIT_REVIEW_DRAFT,
        REFINE_REVIEW_DRAFT,
        PROPOSE_REVIEW_TO_GIT,
        SET_REVIEW_ASSIGNEE,
        REQUEST_REVIEW_EVIDENCE,
    ):
        denied = authorization_error(op, {}, reader)
        assert denied is not None and denied.code == UNAUTHORIZED, op


# --- hy-i4hc: `system` is NOT a token-resolvable role (unforgeable in-process path) ---


def test_a_bearer_asserted_system_role_never_yields_system_privileges(monkeypatch):
    # THE fix: a verified bearer token whose roles claim contains "system" becomes a
    # PLAIN Principal -- exactly what oidc.verify_bearer constructs, never the
    # SYSTEM_PRINCIPAL singleton -- so it resolves `system` against the PUBLIC ROLES,
    # finds nothing, and is denied both a read and a review. An IdP asserting "system"
    # cannot reach the in-process system grant, so no network review-authoring.
    from hyperset.security.authz import SYSTEM_PRINCIPAL
    from hyperset.transport.operations import EDIT_REVIEW_DRAFT, authorization_error

    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    spoof = Principal(subject="attacker", issuer="https://evil.example/", roles=("system",))
    assert spoof is not SYSTEM_PRINCIPAL

    read_params = {"query": QUESTION, "directive": DIRECTIVE}
    assert authorization_error(RESOLVE, read_params, spoof) is not None  # denied a READ
    assert authorization_error(EDIT_REVIEW_DRAFT, {}, spoof) is not None  # denied REVIEW

    # The genuine in-process singleton IS authorized (read + review) -- via its own
    # identity-only registry, which the token can never reach.
    assert authorization_error(RESOLVE, read_params, SYSTEM_PRINCIPAL) is None
    assert authorization_error(EDIT_REVIEW_DRAFT, {}, SYSTEM_PRINCIPAL) is None


# --- hy-87us (F3): the non-human service identity, least-privilege read-only ---


def test_a_service_identity_reads_but_is_denied_review(monkeypatch):
    # A verified service Principal (roles=("service",)) reads governed context but is
    # DENIED review-authoring -- least privilege, distinct from the human reviewer, so a
    # service token can never author a proposal. (configure is likewise not a service
    # grant; that action lands with hy-2nqb.)
    from hyperset.transport.operations import EDIT_REVIEW_DRAFT, authorization_error

    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    service = Principal("svc-client-id", "https://idp.example/", roles=("service",))
    assert (
        authorization_error(RESOLVE, {"query": QUESTION, "directive": DIRECTIVE}, service) is None
    )
    denied = authorization_error(EDIT_REVIEW_DRAFT, {}, service)
    assert denied is not None and denied.code == UNAUTHORIZED


# --- hy-2nqb: ADMIN authorization on the write-back-config write path ---


def test_admin_config_write_requires_an_admin_configure_grant(monkeypatch):
    # The write-back target write (admin deployment settings) requires the `configure`
    # action, which only `admin` holds. Off => the local-only dev shortcut (no gate);
    # on => an unauthenticated caller and every insufficient role are denied, only admin
    # is allowed.
    from hyperset.transport.operations import admin_config_authorization_error

    # OFF (default, loopback dev): no gate, the write is unauthenticated.
    monkeypatch.delenv("HYPERSET_AUTHZ_ENABLED", raising=False)
    assert admin_config_authorization_error(None) is None

    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    # Unauthenticated and insufficient roles are denied.
    assert admin_config_authorization_error(None) is not None
    for role in ("reader", "explorer", "reviewer", "git_owner"):
        denied = admin_config_authorization_error(Principal("u", "i", roles=(role,)))
        assert denied is not None and denied.code == UNAUTHORIZED, role
    # Admin (configure grant) is allowed.
    assert admin_config_authorization_error(Principal("a", "i", roles=("admin",))) is None


def test_the_admin_configure_grant_does_not_leak_into_read_or_review(monkeypatch):
    # Least privilege: `admin` configures but is NOT a reviewer (no review-authoring) and
    # a reviewer/reader cannot configure. The three actions stay distinct.
    from hyperset.security.authz import CONFIGURE, READ, REVIEW, Resource, authorize, roles_for

    resource = Resource(domain="revenue")
    admin = Principal("a", "i", roles=("admin",))
    reviewer = Principal("r", "i", roles=("reviewer",))
    assert authorize(admin, CONFIGURE, resource, roles_for(admin)).allowed is True
    assert authorize(admin, REVIEW, resource, roles_for(admin)).allowed is False
    assert authorize(reviewer, CONFIGURE, resource, roles_for(reviewer)).allowed is False
    assert authorize(reviewer, READ, resource, roles_for(reviewer)).allowed is True


# --- hy-a607k: the explicit approved-reviewer ALLOWLIST, ANDed with the reviewer role ---


def _configure_allowlist(tmp_path, monkeypatch, *identities):
    from hyperset.security.reviewer_allowlist import ALLOWLIST_ENV

    path = tmp_path / "reviewers.allow"
    path.write_text("\n".join(identities) + "\n", encoding="utf-8")
    monkeypatch.setenv(ALLOWLIST_ENV, str(path))


def test_a_configured_allowlist_gates_the_review_ops_in_addition_to_the_role(tmp_path, monkeypatch):
    """With the allowlist configured, a reviewer must ALSO be listed: an on-list reviewer
    authors every review op, an off-list reviewer (same role) is denied each, and the
    in-process system identity is exempt. The allowlist is ANDed with the role, never a
    grant on its own -- so a READER whose identity is on the list is STILL denied (no
    REVIEW grant)."""
    from hyperset.security.authz import SYSTEM_PRINCIPAL
    from hyperset.transport.operations import (
        EDIT_REVIEW_DRAFT,
        PROPOSE_REVIEW_TO_GIT,
        REFINE_REVIEW_DRAFT,
        SET_REVIEW_ASSIGNEE,
        authorization_error,
    )

    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    _configure_allowlist(tmp_path, monkeypatch, "good@https://iss", "reader-id@https://iss")

    on_list = Principal("good", "https://iss", roles=("reviewer",))
    off_list = Principal("bad", "https://iss", roles=("reviewer",))
    listed_reader = Principal("reader-id", "https://iss", roles=("reader",))

    for op in (EDIT_REVIEW_DRAFT, REFINE_REVIEW_DRAFT, PROPOSE_REVIEW_TO_GIT, SET_REVIEW_ASSIGNEE):
        assert authorization_error(op, {}, on_list) is None, op
        assert authorization_error(op, {}, SYSTEM_PRINCIPAL) is None, op
        denied_off = authorization_error(op, {}, off_list)
        assert denied_off is not None and denied_off.code == UNAUTHORIZED, op
        # On the list but only a reader: the role AND is what still denies it.
        denied_reader = authorization_error(op, {}, listed_reader)
        assert denied_reader is not None and denied_reader.code == UNAUTHORIZED, op


def test_the_allowlist_does_not_touch_governed_reads(tmp_path, monkeypatch):
    # Only the REVIEW action consults the allowlist: an off-list reader (or reviewer) still
    # READS governed context, so the allowlist narrows WHO may author, never who may read.
    from hyperset.transport.operations import RESOLVE, authorization_error

    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    _configure_allowlist(tmp_path, monkeypatch, "only-this@https://iss")
    off_list_reader = Principal("someone", "https://iss", roles=("reader",))
    params = {"query": QUESTION, "directive": DIRECTIVE}
    assert authorization_error(RESOLVE, params, off_list_reader) is None


def test_an_unset_allowlist_is_role_only_and_byte_identical(tmp_path, monkeypatch):
    # Not configured => the allowlist is not enforced: any reviewer authors, exactly as
    # before this slice. And with the whole authz gate off, the allowlist is never even
    # consulted (the gate returns before it).
    from hyperset.security.reviewer_allowlist import ALLOWLIST_ENV
    from hyperset.transport.operations import EDIT_REVIEW_DRAFT, authorization_error

    monkeypatch.delenv(ALLOWLIST_ENV, raising=False)
    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    any_reviewer = Principal("whoever", "https://iss", roles=("reviewer",))
    assert authorization_error(EDIT_REVIEW_DRAFT, {}, any_reviewer) is None

    # Gate off entirely: even a configured allowlist is inert (no denial).
    _configure_allowlist(tmp_path, monkeypatch, "nobody@https://iss")
    monkeypatch.delenv("HYPERSET_AUTHZ_ENABLED", raising=False)
    assert authorization_error(EDIT_REVIEW_DRAFT, {}, any_reviewer) is None


def test_the_allowlist_gates_opening_the_review_surface(tmp_path, monkeypatch):
    from hyperset.transport.operations import review_surface_authorization_error

    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    _configure_allowlist(tmp_path, monkeypatch, "good@https://iss")
    on_list = Principal("good", "https://iss", roles=("reviewer",))
    off_list = Principal("bad", "https://iss", roles=("reviewer",))

    assert review_surface_authorization_error(on_list) is None
    denied = review_surface_authorization_error(off_list)
    assert denied is not None and denied.code == UNAUTHORIZED


def test_a_malformed_blank_or_undecodable_policy_fails_closed_at_the_gate(tmp_path, monkeypatch):
    """A misconfigured allowlist never fails OPEN (adversary on #456): a blank env, a
    not-UTF-8 file, and a policy with any malformed entry each DENY a legitimate reviewer
    at the shared gate -- so the review ops AND opening /review are refused, on whatever
    transport, rather than silently reverting to role-only or 500ing."""
    from hyperset.security.reviewer_allowlist import ALLOWLIST_ENV
    from hyperset.transport.operations import (
        EDIT_REVIEW_DRAFT,
        authorization_error,
        review_surface_authorization_error,
    )

    monkeypatch.setenv("HYPERSET_AUTHZ_ENABLED", "1")
    reviewer = Principal("good", "https://iss", roles=("reviewer",))
    path = tmp_path / "policy.allow"

    # (a) blank env -- present but whitespace, NOT unset.
    monkeypatch.setenv(ALLOWLIST_ENV, "   ")
    assert authorization_error(EDIT_REVIEW_DRAFT, {}, reviewer).code == UNAUTHORIZED
    assert review_surface_authorization_error(reviewer).code == UNAUTHORIZED

    # (b) not valid UTF-8 -- the decode error must be caught and fail closed, not escape.
    path.write_bytes(b"\xff\xfe\x80 not utf-8")
    monkeypatch.setenv(ALLOWLIST_ENV, str(path))
    assert authorization_error(EDIT_REVIEW_DRAFT, {}, reviewer).code == UNAUTHORIZED

    # (c) a credential-shaped (colon-bearing) entry with a REAL https issuer beside a valid
    # one -- rejected via the subject-class colon exclusion, so the WHOLE policy fails closed.
    path.write_text("good@https://iss\nuser:supersecret@https://issuer.example\n", encoding="utf-8")
    monkeypatch.setenv(ALLOWLIST_ENV, str(path))
    assert authorization_error(EDIT_REVIEW_DRAFT, {}, reviewer).code == UNAUTHORIZED
    assert review_surface_authorization_error(reviewer).code == UNAUTHORIZED
