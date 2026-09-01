"""The authorization schema and a pure fail-closed decision (ADR-0030, hy-m5au;
gate wired in hy-ac2x). `authorize` is the ONE decision the gate at `run_operation`
calls, before dispatch, over a `principal` the caller supplies. THIS cut ships the
gate default-off and the trusted in-process `SYSTEM_PRINCIPAL` only; the LIVE HTTP
bearer/JWT path (an OIDC/JWKS verifier) is deferred to a later cut gated on ADR-0030
ratification (hy-lrho), because a live JWKS fetch is a network trust surface that
should not land inert. So over HTTP the gate is fail-closed: enabled with no bearer
path yet, it denies; disabled (the default), it is a no-op and nothing changes.

It does not move `tools_hash`, which covers only the `RESOLVE_PATH_OPERATIONS` tool
specs (name/description/input_schema): the gate is an executor-level Python check, not
a served directive parameter, so importing `authorize` into `run_operation` adds no
tool and changes no input schema. ADR-0030 is still PROPOSED, so the gate is inert
unless an operator sets `HYPERSET_AUTHZ_ENABLED`; ratification does not change code.

The model is `Principal -> roles -> Role -> grants -> Grant(effect, action, Scope,
conditions)`. A `Scope` field of `None` means "any" at that level, so the single
`reader` role today widens to all governed context and a future grant narrows to a
domain, source, or field -- and adds policy `Condition`s -- with no schema change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum

ENABLED_ENV = "HYPERSET_AUTHZ_ENABLED"
_TRUTHY = {"1", "true", "yes", "on"}


def authz_enabled() -> bool:
    """Whether the authorization gate is engaged. DEFAULT DISABLED: an unset or falsey
    flag leaves the server exactly as it was -- unauthenticated -- so a dev, demo, or
    CI caller is unaffected and the served bytes are unchanged. Only an operator
    opting in turns the gate on, and enabling it in production is itself gated on
    ADR-0030 ratification and the deferred HTTP bearer path (hy-lrho)."""
    return os.environ.get(ENABLED_ENV, "").strip().lower() in _TRUTHY


class Effect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True)
class Principal:
    """A VERIFIED caller identity, derived from an upstream IdP token or assertion.

    Constructing one implies a validated token: unverified input never becomes a
    `Principal` (that check is the transport-boundary verifier, a later slice).
    `issuer` is the OIDC issuer or SAML IdP entity id and is provider-neutral -- no
    issuer is special-cased, so Okta is configuration, not code.
    """

    subject: str
    issuer: str
    roles: tuple[str, ...] = ()
    # The TENANT/WORKSPACE this identity acts in (hq-t6nx, ADR-0037), from a
    # configured OIDC claim. Additive and fail-closed: an unset or absent claim
    # resolves to the single implicit `"default"` workspace, NEVER a wildcard/
    # all-workspaces value -- a caller always acts in exactly one concrete
    # workspace, so cross-workspace data can never leak through a missing claim.
    workspace: str = "default"


@dataclass(frozen=True)
class Resource:
    """What a served operation touches. `domain` is required; `source_ref`/`field`
    are `None` for a whole-domain read and set for a finer-grained one."""

    domain: str
    source_ref: str | None = None
    field: str | None = None
    # The TENANT/WORKSPACE the resource lives in (hq-t6nx, ADR-0037). The OUTERMOST
    # partition; `None` on a resource means "workspace not asserted" and is only
    # covered by a `workspace=None` (any-workspace) scope. Additive: default `None`
    # keeps every existing Resource construction and grant unchanged.
    workspace: str | None = None


@dataclass(frozen=True)
class Scope:
    """The extent a grant covers. `None` at a level means "any" at that level, so an
    all-`None` scope is "all governed context" and setting a field narrows it. The
    extensibility seam: reader-reads-all today, per-domain/source/field/workspace
    tomorrow."""

    domain: str | None = None
    source_ref: str | None = None
    field: str | None = None
    # The TENANT/WORKSPACE this grant is confined to (hq-t6nx, ADR-0037). `None` =
    # any workspace, so every existing grant (Scope()) is unchanged -- a
    # deployment narrows a role to one workspace by setting it. The OUTERMOST level
    # of the same superset seam as domain/source_ref/field.
    workspace: str | None = None

    def covers(self, resource: Resource) -> bool:
        """The safe SUPERSET direction only: a whole-domain scope (field=None) covers
        a field request, never the reverse. It does NOT reconcile a coarse request
        against a finer DENY -- that a field-scoped DENY must also block a
        whole-domain read is the enforcement seam's job (decompose the result and deny
        the whole on any denied constituent, ADR-0030 Decision 4), not `covers`'s."""
        return (
            (self.workspace is None or self.workspace == resource.workspace)
            and (self.domain is None or self.domain == resource.domain)
            and (self.source_ref is None or self.source_ref == resource.source_ref)
            and (self.field is None or self.field == resource.field)
        )


@dataclass(frozen=True)
class Condition:
    """A policy predicate a grant may carry (the second extensibility axis).
    Evaluated against a request context; an unknown operator or a missing key is NOT
    satisfied -- fail closed. Empty in the reader-only model."""

    key: str
    op: str
    value: str

    def holds(self, context: dict) -> bool:
        if self.op == "eq":
            return context.get(self.key) == self.value
        # Unknown operator: fail closed.
        return False


@dataclass(frozen=True)
class Grant:
    effect: Effect
    action: str  # "read" today; extensible (e.g. "resolve", "validate")
    scope: Scope
    conditions: tuple[Condition, ...] = ()


@dataclass(frozen=True)
class Role:
    name: str
    grants: tuple[Grant, ...] = ()


@dataclass(frozen=True)
class Decision:
    """A non-disclosing outcome. `reason` names the DECISION CLASS, never what
    exists -- an unauthorized caller gets the same `denied` for a resource that
    exists and one that does not, so the denial leaks nothing."""

    allowed: bool
    reason: str


# The ACTIONS a grant may name. Two today (hy-dq0r): reading governed context, and
# AUTHORING a review draft/proposal (edit/refine/propose). The action is what makes a
# role more than a name: `reviewer` differs from `explorer` ONLY because it also holds
# a `review` grant. Named as constants because both the grants below and the operation
# action map (`transport.operations.OPERATION_ACTIONS`) read them, and a typo that did
# not match would silently deny (fail closed) rather than error. `configure` (admin
# deployment settings) and `approve`/`merge` are deliberately NOT here: config auth is
# hy-2nqb (a different served surface), and approval/merge authority is a human GitHub
# merge (ADR-0012), never a Hyperset grant.
READ = "read"
REVIEW = "review"
# CONFIGURE authorizes an ADMIN deployment-settings WRITE -- setting the write-back
# target (hy-2nqb). It is distinct from REVIEW (authoring a proposal is a reviewer act,
# not a config act) and from approve/merge (which is a human GitHub merge, never a
# Hyperset grant). Only `admin` holds it.
CONFIGURE = "configure"

# The role vocabulary beyond `reader` (hy-dq0r, F1 under overseer ruling hq-l4g2). Each
# is LEAST PRIVILEGE and all-domain by default; a deployment narrows a role to specific
# domains by defining a domain-scoped variant in its own registry (see
# `test_authz_gate`'s scoped roles), which the existing `Scope`/deny-the-whole seam
# already enforces. WHERE a deployment's per-principal grants ultimately live -- a
# Git-owned policy per F1's recommendation -- is a later, larger source slice; this slice
# ships the reviewed VOCABULARY in code so the named identities resolve to grants at all
# (an IdP token carrying `admin` must map to *something*, or an admin is denied every
# read -- fail closed the wrong way). The FIRST Git-owned per-principal policy has since
# landed for the REVIEW action: `security/reviewer_allowlist.py` (hy-a607k) gates the
# review surface + authoring ops on an explicit approved-`subject@issuer` allowlist,
# ANDed with the reviewer role, default-off and fail-closed.
READER = Role(name="reader", grants=(Grant(Effect.ALLOW, READ, Scope()),))
# The read-only end user (#78 "Explorer"): reads governed context, authors no review,
# configures nothing. Same grants as `reader`, a distinct name so an IdP that emits
# `explorer` resolves.
EXPLORER = Role(name="explorer", grants=(Grant(Effect.ALLOW, READ, Scope()),))
# Reviewer: reads, and AUTHORS review drafts/proposals (edit/refine/propose). It never
# approves or merges -- there is no such grant; approval is a human GitHub merge.
REVIEWER = Role(
    name="reviewer",
    grants=(Grant(Effect.ALLOW, READ, Scope()), Grant(Effect.ALLOW, REVIEW, Scope())),
)
# Admin/steward: reads governed context AND configures the deployment (the write-back
# target, hy-2nqb) -- the `configure` grant. It is NOT semantic-approval authority, so it
# holds no `review` grant: an admin sets the target repo, a reviewer authors proposals,
# and a human GitHub merge approves. Least privilege keeps those three distinct.
ADMIN = Role(
    name="admin",
    grants=(Grant(Effect.ALLOW, READ, Scope()), Grant(Effect.ALLOW, CONFIGURE, Scope())),
)
# Git owner: reads governed context. Its authority -- approving/merging governed
# meaning -- is exercised in GitHub (ADR-0012), never as a Hyperset grant, so within
# this gate it is a reader.
GIT_OWNER = Role(name="git_owner", grants=(Grant(Effect.ALLOW, READ, Scope()),))
# A NON-HUMAN service identity (#78 F3, hq-l4g2 rec (a)): a service-to-service or
# evaluation caller that authenticates as a verified `Principal` via OIDC
# client-credentials -- the SAME bearer path a human token takes (oidc.verify_bearer).
# It is DISTINCT from the human roles so a deployment can scope and audit machine callers
# separately, and LEAST PRIVILEGE: read-only. A service holds no `review` and (once
# hy-2nqb lands) no `configure` grant -- authoring proposals and configuring the
# deployment are human acts, so a service token can never do them.
#
# `service` IS token-resolvable (unlike `system`, which is object-identity-only) because a
# service authenticates with a real token -- but it is MACHINE-ONLY: a human bearer must
# never become a service identity by merely listing `roles=["service"]`. That is enforced
# at the verifier (hy-okm6): `service` is stripped from a token's roles UNLESS the token
# is a genuine client-credentials grant (see `CLIENT_CREDENTIALS_ONLY_ROLES` below and
# `oidc._is_client_credentials`). So the registry can hold `service` while a human bearer
# still cannot assert it -- the discriminator is at authentication, not in the grant table.
SERVICE = Role(name="service", grants=(Grant(Effect.ALLOW, READ, Scope()),))
# The trusted in-process identity's role (read + review over stdio/eval). It is
# DELIBERATELY NOT in the public `ROLES` registry below (hy-i4hc): `ROLES` is the
# registry a VERIFIED BEARER TOKEN's roles claim maps into (oidc.verify_bearer ->
# Principal.roles -> authorize over ROLES), so a role reachable from `ROLES` is a role a
# token can assert. The `system` role therefore lives in its own `_SYSTEM_ROLES`
# registry, resolvable ONLY on the in-process identity path (`is SYSTEM_PRINCIPAL`), so a
# bearer token whose claim says roles=["system"] -- a plain `Principal`, never this
# singleton object -- resolves `system` against `ROLES`, finds nothing, and is denied.
SYSTEM = Role(
    name="system",
    grants=(Grant(Effect.ALLOW, READ, Scope()), Grant(Effect.ALLOW, REVIEW, Scope())),
)

# The registry the gate resolves a VERIFIED PRINCIPAL's role NAMES against. A role name a
# principal carries but this map does not know resolves to no grants -- an unknown role
# is a silent no-match and the caller is denied (fail closed). `system` is intentionally
# absent: it is not a token-assertable role.
ROLES: dict[str, Role] = {
    role.name: role for role in (READER, EXPLORER, REVIEWER, ADMIN, GIT_OWNER, SERVICE)
}

# Role names a HUMAN bearer may NEVER assert through its roles claim: they identify a
# non-human caller and are honored only for a proven client-credentials token. The
# verifier (`oidc._roles_from_claims`) strips any name in this set from a token's roles
# unless `oidc._is_client_credentials(claims)` holds, so a human token carrying
# `roles=["service"]` (or `["service","reviewer"]`) resolves `service` to nothing and
# keeps only its human roles. This is the token-path analogue of `_SYSTEM_ROLES`: `system`
# is separated by OBJECT IDENTITY (unforgeable, never token-reachable); `service` is
# separated by TOKEN KIND (client-credentials vs a human bearer), because a service DOES
# present a token. Kept here, beside `ROLES`, so the machine-only policy lives with the
# registry rather than being buried in the verifier (hy-okm6, mirrors hy-i4hc).
CLIENT_CREDENTIALS_ONLY_ROLES: frozenset[str] = frozenset({SERVICE.name})

# The in-process-ONLY registry, reachable solely through the `SYSTEM_PRINCIPAL` object
# identity at the gate. Keeping `system` here rather than in `ROLES` is the unforgeable
# separation: object identity cannot be forged by a token (no transport constructs this
# singleton from external input), so a token-asserted `system` role can never reach these
# grants.
_SYSTEM_ROLES: dict[str, Role] = {SYSTEM.name: SYSTEM}

# The trusted in-process identity for a local, non-network caller: the evaluation
# executor and a stdio subprocess, which run with full process trust and carry no bearer
# token. Its `system` role (read + review) is authorized against `_SYSTEM_ROLES` ONLY
# when the caller IS this exact object -- so a review op over trusted stdio is allowed
# while a network token asserting `system` is not. It is NEVER derived from external
# input and no transport constructs it, so it cannot be a bypass vector -- only
# in-process code that already runs trusted can pass it into `run_operation`.
SYSTEM_PRINCIPAL = Principal(subject="system", issuer="hyperset:in-process", roles=("system",))


def roles_for(principal: Principal | None) -> dict[str, Role]:
    """The role registry a principal's names resolve against: the in-process-only
    `_SYSTEM_ROLES` for the trusted `SYSTEM_PRINCIPAL` singleton (recognized by OBJECT
    IDENTITY, which a token cannot forge), and the public token-resolvable `ROLES` for
    everyone else. This is the one place the two registries are chosen between, so the
    `system` role can never leak into the token path (hy-i4hc)."""
    return _SYSTEM_ROLES if principal is SYSTEM_PRINCIPAL else ROLES


def authorize(
    principal: Principal | None,
    action: str,
    resource: Resource,
    roles: dict[str, Role],
    *,
    context: dict | None = None,
) -> Decision:
    """Pure, fail-closed authorization. DENY unless an explicit ALLOW grant matches
    the action and scope with its conditions satisfied AND no DENY grant matches
    (deny wins). No principal, an unknown role, or no matching grant is a complete,
    uniform denial that discloses nothing about the resource. This decides; it
    enforces nothing -- a transport boundary wires it in a later slice.
    """
    if principal is None:
        return Decision(False, "unauthenticated")
    context = context or {}
    matched_allow = False
    for role_name in principal.roles:
        role = roles.get(role_name)
        if role is None:
            continue
        for grant in role.grants:
            if grant.action != action or not grant.scope.covers(resource):
                continue
            if not all(condition.holds(context) for condition in grant.conditions):
                continue
            if grant.effect is Effect.DENY:
                # Deny wins immediately, uniform regardless of the resource.
                return Decision(False, "denied")
            matched_allow = True
    return Decision(matched_allow, "authorized" if matched_allow else "denied")
