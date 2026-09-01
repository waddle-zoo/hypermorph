"""ADR-0030's acceptance list for the pure decision function `authorize` (hy-m5au).
This exercises the decision in isolation; the gate wiring landed in hy-ac2x and is
exercised separately (`tests/unit/transport/test_authz_gate.py` for the
`run_operation` gate). The live OIDC/JWT bearer verifier is a deferred cut (hy-lrho),
so here the boundary is modelled as `principal is None` (no verified identity).
"""

from __future__ import annotations

from hyperset.security.authz import (
    READER,
    Condition,
    Decision,
    Effect,
    Grant,
    Principal,
    Resource,
    Role,
    Scope,
    authorize,
)

ROLES = {"reader": READER}


def _reader(issuer: str = "https://issuer.example") -> Principal:
    return Principal(subject="u1", issuer=issuer, roles=("reader",))


# 1 + 4 (boundary + complete, leak-free denial)
def test_no_verified_identity_is_a_complete_denial():
    decision = authorize(None, "read", Resource("revenue"), ROLES)
    assert decision == Decision(allowed=False, reason="unauthenticated")


def test_an_unauthorized_caller_gets_the_same_denial_for_existing_and_absent_resources():
    # A principal with no matching grant. The denial must be IDENTICAL for a resource
    # that "exists" and one that does not, so it discloses nothing about what exists.
    stranger = Principal("u2", "https://issuer.example", roles=("nobody",))
    existing = authorize(stranger, "read", Resource("revenue"), ROLES)
    absent = authorize(stranger, "read", Resource("does_not_exist"), ROLES)
    assert existing == absent == Decision(allowed=False, reason="denied")


# 2 (provider-neutral)
def test_a_reader_from_any_issuer_authorizes_identically():
    okta = authorize(_reader("https://acme.okta.com"), "read", Resource("revenue"), ROLES)
    other = authorize(
        _reader("https://login.other-idp.example"), "read", Resource("revenue"), ROLES
    )
    assert okta.allowed is True
    assert okta.allowed == other.allowed


# 3 (reader reads all governed context)
def test_a_reader_may_read_every_governed_domain_and_finer_resources():
    reader = _reader()
    for resource in (
        Resource("revenue"),
        Resource("supply_chain_lead_time"),
        Resource("revenue", source_ref="table:postgres:analytics.public.finance_orders_daily"),
        Resource("revenue", source_ref="table:postgres:x", field="customer_email"),
    ):
        assert authorize(reader, "read", resource, ROLES).allowed is True


# 5 (fail-closed default)
def test_an_unknown_role_denies():
    p = Principal("u", "iss", roles=("analyst",))  # not in ROLES
    assert authorize(p, "read", Resource("revenue"), ROLES).allowed is False


def test_a_deny_grant_wins_over_a_co_matching_allow():
    role = Role(
        "mixed",
        grants=(
            Grant(Effect.ALLOW, "read", Scope()),
            Grant(Effect.DENY, "read", Scope(domain="revenue")),
        ),
    )
    roles = {"mixed": role}
    p = Principal("u", "iss", roles=("mixed",))
    assert authorize(p, "read", Resource("revenue"), roles).allowed is False
    assert authorize(p, "read", Resource("supply_chain_lead_time"), roles).allowed is True


def test_an_action_the_grant_does_not_name_is_denied():
    # reader grants "read"; a "resolve" action is not covered.
    assert authorize(_reader(), "resolve", Resource("revenue"), ROLES).allowed is False


def test_a_condition_fails_closed_and_gates_an_allow():
    conditional = Role(
        "conditional",
        grants=(
            Grant(Effect.ALLOW, "read", Scope(), conditions=(Condition("env", "eq", "prod"),)),
        ),
    )
    roles = {"conditional": conditional}
    p = Principal("u", "iss", roles=("conditional",))
    assert authorize(p, "read", Resource("revenue"), roles, context={"env": "prod"}).allowed is True
    assert authorize(p, "read", Resource("revenue"), roles, context={"env": "dev"}).allowed is False
    assert authorize(p, "read", Resource("revenue"), roles, context={}).allowed is False
    # Unknown operator: fail closed even when the key matches.
    weird = Role(
        "w", grants=(Grant(Effect.ALLOW, "read", Scope(), (Condition("env", "gt", "x"),)),)
    )
    p2 = Principal("u", "iss", roles=("w",))
    assert (
        authorize(p2, "read", Resource("revenue"), {"w": weird}, context={"env": "z"}).allowed
        is False
    )


# 6 (extensibility seam: reader -> per-domain / per-field, no schema change)
def test_a_per_domain_grant_restricts_a_reader_to_a_subset():
    scoped = Role("revenue_reader", grants=(Grant(Effect.ALLOW, "read", Scope(domain="revenue")),))
    roles = {"revenue_reader": scoped}
    p = Principal("u", "iss", roles=("revenue_reader",))
    assert authorize(p, "read", Resource("revenue"), roles).allowed is True
    assert authorize(p, "read", Resource("supply_chain_lead_time"), roles).allowed is False


def test_a_per_field_grant_narrows_further():
    scoped = Role(
        "field_reader",
        grants=(Grant(Effect.ALLOW, "read", Scope(domain="revenue", field="region")),),
    )
    roles = {"field_reader": scoped}
    p = Principal("u", "iss", roles=("field_reader",))
    assert authorize(p, "read", Resource("revenue", field="region"), roles).allowed is True
    assert authorize(p, "read", Resource("revenue", field="customer_email"), roles).allowed is False
    assert (
        authorize(p, "read", Resource("revenue"), roles).allowed is False
    )  # whole-domain != field


# 8 (invariant: the stub is off the resolve path)
def test_the_authz_stub_does_not_move_the_tools_hash_or_schema_version():
    from hyperset.bundle.schema import SCHEMA_VERSION
    from hyperset.planner.loop import RESOLVE_PATH_OPERATIONS, tools_hash

    assert tools_hash() == "sha256:fe930a003b731211"
    assert SCHEMA_VERSION == 26
    # The stub is not a served operation on the hashed allowlist.
    assert not any("authz" in str(op).lower() for op in RESOLVE_PATH_OPERATIONS)


# --- hq-t6nx (ADR-0037): the workspace level of the Scope/Resource seam ---


def test_a_workspace_scoped_grant_covers_only_its_workspace():
    # A grant confined to workspace 'acme' covers an 'acme' resource and DENIES a
    # 'beta' one -- the outermost partition of the same superset seam.
    acme_reader = Role(name="ar", grants=(Grant(Effect.ALLOW, "read", Scope(workspace="acme")),))
    registry = {"ar": acme_reader}
    principal = Principal(subject="u", issuer="i", roles=("ar",), workspace="acme")
    assert authorize(principal, "read", Resource("revenue", workspace="acme"), registry).allowed
    assert not authorize(principal, "read", Resource("revenue", workspace="beta"), registry).allowed


def test_a_workspaceless_grant_is_unchanged_by_the_new_level():
    # Additive: an existing grant (Scope() with workspace=None) covers any workspace,
    # so every role defined before this slice behaves exactly as before.
    assert Scope().covers(Resource("revenue"))
    assert Scope().covers(Resource("revenue", workspace="acme"))
    # And a workspace-scoped grant does NOT cover a workspace-less resource.
    assert not Scope(workspace="acme").covers(Resource("revenue"))
