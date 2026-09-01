"""Bind the feature-parity audit's load-bearing claims to the code it cites.

`docs/development/feature-parity-audit.md` is an adoption-accuracy artifact: a
reviewer checks every row against the code. Prose alone rots silently -- a flag
default flips, a role is added, an inert producer gets wired -- and the doc keeps
claiming the old truth. These bind the claims the audit most depends on to
BEHAVIOR (flags exercised, producers run, keys that do or do not survive a
restart), not to comment strings, so a contradicting change reddens here and
forces a re-audit rather than shipping a false claim (hy-w8q2).

The claims that need a real database are bound behaviorally in
`tests/postgres/test_context_bundle.py`, where the resolve path is exercised
against Postgres:
- `test_the_unwired_reconciliation_kinds_never_reach_a_resolved_bundle` -- the two
  WIRED producers reach a resolved bundle while the three inert ones never do;
- `test_a_persisted_processor_finding_reaches_the_bundle_as_a_projected_conflict`
  -- a persisted processor finding is PROJECTED into `conflicts` through
  `resolver._conflict` -> `reconcile()` (the third reconciliation path);
- the existing prohibited/source-deleted conflict tests.

Deliberately NOT line-numbered and NOT comment-string matching: the audit cites
those for a human reader, but a test asserting them would rot on any unrelated
edit and pass on a gutted implementation.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AUDIT = REPO / "docs" / "development" / "feature-parity-audit.md"
ENABLEMENT = REPO / "docs" / "development" / "end-user-auth-enablement.md"


def test_authz_gate_is_default_off_and_is_a_real_switch(monkeypatch):
    """Section 2: the authz gate is DEFAULT-OFF -- unset => unauthenticated."""
    from hyperset.security import authz

    monkeypatch.delenv(authz.ENABLED_ENV, raising=False)
    assert authz.authz_enabled() is False, "unset flag must leave the server unauthenticated"
    monkeypatch.setenv(authz.ENABLED_ENV, "true")
    assert authz.authz_enabled() is True, "the flag must be the real switch (not a dead constant)"


def test_bearer_principal_is_none_while_the_gate_is_off(monkeypatch):
    """Section 2: `principal_from_bearer` returns None unless authz is on -- so a
    governed read is answered with no authenticated principal by default."""
    from hyperset.security import authz, oidc

    monkeypatch.delenv(authz.ENABLED_ENV, raising=False)
    assert oidc.principal_from_bearer("Bearer whatever") is None


def test_the_role_vocabulary_is_the_posture_roles():
    """Section 2 role-model row, UPDATED (hy-dq0r): the role vocabulary beyond
    `reader` landed. The audit's PROPOSED-ONLY row is reconciled to the #78 posture
    roles; this guard now pins that exact set, so another role landing (or one
    vanishing) reds here and the audit row is re-checked again."""
    from hyperset.security import authz

    # The PUBLIC, token-resolvable registry is the five end-user roles plus the non-human
    # `service` identity (hy-87us, F3). `system` is deliberately NOT here (hy-i4hc): it is
    # the in-process identity's role, kept in a separate registry a bearer token can never
    # reach, so a token asserting "system" resolves to nothing and is denied.
    assert set(authz.ROLES) == {"reader", "explorer", "reviewer", "admin", "git_owner", "service"}
    assert "system" not in authz.ROLES
    assert authz.SYSTEM.name not in authz.ROLES and authz.SYSTEM.name in authz._SYSTEM_ROLES
    # The distinguishing grant: only `reviewer` may author a review among the public
    # roles; the read-only roles -- including the machine `service` -- hold no `review`
    # grant. Least privilege, not six names for one role.
    assert authz.REVIEW in {grant.action for grant in authz.REVIEWER.grants}
    for read_only in (authz.EXPLORER, authz.ADMIN, authz.GIT_OWNER, authz.SERVICE):
        assert authz.REVIEW not in {grant.action for grant in read_only.grants}


def test_resolve_path_allowlist_is_exactly_the_three_hashed_ops():
    """Section 1: only catalog/resolve/validate are in the hashed allowlist."""
    from hyperset.planner.loop import RESOLVE_PATH_OPERATIONS
    from hyperset.transport.operations import CATALOG, RESOLVE, VALIDATE

    assert RESOLVE_PATH_OPERATIONS == (CATALOG, RESOLVE, VALIDATE)


def test_the_two_wired_reconciliation_producers_actually_emit_a_conflict():
    """Section 4: `prohibited_but_referenced` and `source_deleted_while_governed`
    are real join logic, not stubs -- they emit a conflict when the two sides
    disagree and stay silent when they agree. (That these reach a RESOLVED bundle,
    and that the three inert kinds do not, is bound in tests/postgres.)"""
    from hyperset.bundle.reconcile import (
        PROHIBITED_BUT_REFERENCED,
        SOURCE_DELETED_WHILE_GOVERNED,
        prohibited_but_referenced,
        source_deleted_while_governed,
    )

    prohibited = [{"ref": "table:x", "reason": "deprecated source"}]
    # Referenced by a live asset => a conflict; nothing referencing it => silence.
    emitted = prohibited_but_referenced(
        prohibited, referenced_by={"table:x": ["chart:1"]}, commit_sha="abc"
    )
    assert [e["kind"] for e in emitted] == [PROHIBITED_BUT_REFERENCED]
    assert prohibited_but_referenced(prohibited, referenced_by={}, commit_sha="abc") == []

    deleted = [{"ref": "table:y", "asset_type": "dataset", "deleted_at": "2026-01-01T00:00:00"}]
    gone = source_deleted_while_governed(deleted, prohibited_refs=[], commit_sha="abc")
    assert [e["kind"] for e in gone] == [SOURCE_DELETED_WHILE_GOVERNED]
    # A deleted source the commit PROHIBITS is agreement, not a conflict.
    agreed = source_deleted_while_governed(deleted, prohibited_refs=["table:y"], commit_sha="abc")
    assert agreed == []


def test_the_secret_box_ephemeral_default_does_not_survive_a_restart(monkeypatch):
    """Section 2 secret-at-rest row: with no configured KEK the default key is
    EPHEMERAL -- a stored secret does not survive a restart -- and a configured KEK
    does survive. Behavioral: encrypt, simulate a restart, observe the difference."""
    from hyperset.security import secret_box

    # Unset KEK: ephemeral in-memory key, not "configured".
    monkeypatch.delenv(secret_box.KEK_ENV, raising=False)
    monkeypatch.setattr(secret_box, "_ephemeral_kek", None, raising=False)
    monkeypatch.setattr(secret_box, "_warned", True, raising=False)
    assert secret_box.key_is_configured() is False
    ciphertext, nonce = secret_box.encrypt("write-back-token")
    # Restart: the ephemeral key is regenerated, so the old secret is unreadable.
    monkeypatch.setattr(secret_box, "_ephemeral_kek", None, raising=False)
    import pytest

    with pytest.raises(secret_box.SecretBoxError):
        secret_box.decrypt(ciphertext, nonce)

    # A configured durable KEK survives the same restart.
    import base64
    import os

    monkeypatch.setenv(secret_box.KEK_ENV, base64.b64encode(os.urandom(32)).decode())
    assert secret_box.key_is_configured() is True
    ct2, nonce2 = secret_box.encrypt("write-back-token")
    monkeypatch.setattr(secret_box, "_ephemeral_kek", None, raising=False)
    assert secret_box.decrypt(ct2, nonce2) == "write-back-token"


def test_admin_surface_is_a_routing_gate_plus_write_authz_when_enabled():
    """Section 2 admin row: `/admin` is served only behind the playground flag and the
    SURFACE is a routing split (`/admin/api` in the playground api paths), not
    authentication. The write-back-config WRITE is now ADDITIONALLY admin-authorized
    server-side when the authz gate is on (hy-2nqb); the gate is off by default, so the
    write stays unauthenticated on loopback dev -- the local-only shortcut."""
    from hyperset.transport import http

    assert http.ADMIN_ROOT_PATH == "/admin"
    assert http.ADMIN_API_PATH == "/admin/api"
    assert http.ADMIN_API_PATH in http.PLAYGROUND_API_PATHS, (
        "the admin surface is the playground routing split, a surface gate; if this "
        "changes the audit's admin row must move"
    )
    # The write path's authz decision exists and is a real function (the enforcement the
    # audit's admin row now claims).
    from hyperset.transport.operations import admin_config_authorization_error

    assert callable(admin_config_authorization_error)


def test_enablement_track_doc_exists_and_names_the_forks():
    """Section 2 points at the authz ENABLEMENT track; its design doc is real."""
    assert ENABLEMENT.exists(), "the enablement design doc the audit points at is missing"
    text = ENABLEMENT.read_text(encoding="utf-8")
    for fork in ("F1", "F2", "F3", "F4", "F5"):
        assert fork in text, f"enablement doc no longer names fork {fork}"
    assert "precondition #4" in text
    assert "IdP-role mapping" in text


def test_audit_points_at_the_tracking_beads():
    """The audit must name the tracks it points at, or the pointers silently drop."""
    text = AUDIT.read_text(encoding="utf-8")
    for ref in (
        "hy-tjow",  # authz enablement track (#78 / F1-F5)
        "hy-2nqb",  # admin write-path auth
        "hy-u26p",  # up-demo demonstrates no finding/conflict/review task
        "hy-z3wy",  # ownership_mismatch activation fork
        "hy-yjkv",  # grain_mismatch activation fork
        "hy-kh9k",  # freshness_stale activation fork
        "end-user-auth-enablement.md",
        "linked_evidence.conflicts",
    ):
        assert ref in text, f"the audit no longer references {ref!r}"
