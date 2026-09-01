"""The explicit approved-reviewer allowlist (hy-a607k): a Git-owned-by-reference,
default-off, fail-closed policy of approved `subject@issuer` identities."""

from __future__ import annotations

from hyperset.security.authz import SYSTEM_PRINCIPAL, Principal
from hyperset.security.reviewer_allowlist import ALLOWLIST_ENV, approves, reviewer_allowlist

_LISTED = Principal(subject="auth0|abc123", issuer="https://issuer.example", roles=("reviewer",))
_UNLISTED = Principal(subject="mallory", issuer="https://issuer.example", roles=("reviewer",))


def _write_allowlist(tmp_path, monkeypatch, body: str):
    path = tmp_path / "reviewers.allow"
    path.write_text(body, encoding="utf-8")
    monkeypatch.setenv(ALLOWLIST_ENV, str(path))
    return path


def test_unconfigured_is_not_enforced(monkeypatch):
    # Env unset => None => the allowlist is not enforced, so a deployment that has not
    # opted in behaves exactly as before (role-only): approves ANY principal.
    monkeypatch.delenv(ALLOWLIST_ENV, raising=False)
    assert reviewer_allowlist() is None
    assert approves(_LISTED) is True
    assert approves(_UNLISTED) is True
    assert approves(None) is True


def test_a_configured_allowlist_admits_only_listed_identities(tmp_path, monkeypatch):
    _write_allowlist(
        tmp_path,
        monkeypatch,
        "# approved reviewers\nauth0|abc123@https://issuer.example\n\nother@https://iss.two\n",
    )
    assert reviewer_allowlist() == frozenset(
        {"auth0|abc123@https://issuer.example", "other@https://iss.two"}
    )
    assert approves(_LISTED) is True
    assert approves(_UNLISTED) is False
    # An unauthenticated caller is never on the list.
    assert approves(None) is False


def test_a_configured_but_empty_or_unreadable_allowlist_fails_closed(tmp_path, monkeypatch):
    # Empty file => approves nobody (an empty policy is not an open one).
    _write_allowlist(tmp_path, monkeypatch, "# nobody yet\n\n")
    assert reviewer_allowlist() == frozenset()
    assert approves(_LISTED) is False

    # Configured but missing file => fail closed (deny), never fall open.
    monkeypatch.setenv(ALLOWLIST_ENV, str(tmp_path / "does-not-exist.allow"))
    assert reviewer_allowlist() == frozenset()
    assert approves(_LISTED) is False


def test_the_in_process_system_identity_is_always_admitted(tmp_path, monkeypatch):
    # The trusted eval/stdio identity is not a network reviewer and must never be stranded
    # by a configured allowlist -- the same exemption it has from the token role registry.
    _write_allowlist(tmp_path, monkeypatch, "someone-else@https://iss\n")
    assert approves(SYSTEM_PRINCIPAL) is True


def test_a_blank_env_fails_closed_not_role_only(monkeypatch):
    # A PRESENT but blank/whitespace env is a misconfiguration, distinct from UNSET: it must
    # NOT silently become role-only. Unset => None (role-only); blank => empty set (deny).
    monkeypatch.setenv(ALLOWLIST_ENV, "   ")
    assert reviewer_allowlist() == frozenset()
    assert approves(_LISTED) is False
    # ...and unset is the ONLY role-only case.
    monkeypatch.delenv(ALLOWLIST_ENV, raising=False)
    assert reviewer_allowlist() is None


def test_invalid_utf8_policy_fails_closed(tmp_path, monkeypatch):
    # A file that is not valid UTF-8 raises UnicodeDecodeError inside the loader; it must be
    # caught and fail the policy CLOSED, never escape the authz decision as a 500.
    path = tmp_path / "reviewers.allow"
    path.write_bytes(b"\xff\xfe not utf-8 \x80\x81")
    monkeypatch.setenv(ALLOWLIST_ENV, str(path))
    assert reviewer_allowlist() == frozenset()
    assert approves(_LISTED) is False


def test_a_malformed_or_pii_entry_fails_the_whole_policy_closed(tmp_path, monkeypatch):
    # ANY entry that is not a well-formed opaque subject@issuer poisons the WHOLE policy:
    # it is never inserted unchanged (that would reintroduce the #455 malformed-subject
    # path), and a partly-garbage policy approves NOBODY rather than a subset.
    for bad_entry in (
        "junk",  # no @issuer
        # A credential-shaped subject with a REAL https issuer: rejected because the
        # subject class excludes ':' (adversary round 2 -- the `:` had made it valid).
        "user:supersecret@https://issuer.example",
        "https://user:supersecret@host/repo",  # a credential URL
        "sub@https://user:tok@issuer.example",  # userinfo in the issuer
    ):
        path = tmp_path / "reviewers.allow"
        # A VALID reviewer sits beside the bad line: the whole policy still fails closed.
        path.write_text(f"auth0|abc123@https://issuer.example\n{bad_entry}\n", encoding="utf-8")
        monkeypatch.setenv(ALLOWLIST_ENV, str(path))
        assert reviewer_allowlist() == frozenset(), bad_entry
        assert approves(_LISTED) is False, bad_entry
        # The malformed value never survives into the set.
        assert bad_entry not in (reviewer_allowlist() or frozenset())


def test_the_deliberate_boundary_numeric_hyphen_ok_colon_rejected(tmp_path, monkeypatch):
    # The boundary the mayor drew (#456): a purely numeric/hyphen subject with a CLEAN https
    # issuer is a legitimate IdP identity -- the system accepts whatever the IdP asserts and
    # does NOT judge an opaque sub as PII (the #455 ruling) -- while a COLON-bearing,
    # credential-shaped subject is rejected. Both have a real https issuer, so this is the
    # colon distinction, not the https requirement.
    path = tmp_path / "reviewers.allow"

    # ACCEPTED: numeric + hyphen subject, clean https issuer.
    path.write_text("123-45-6789@https://issuer.example\n", encoding="utf-8")
    monkeypatch.setenv(ALLOWLIST_ENV, str(path))
    assert reviewer_allowlist() == frozenset({"123-45-6789@https://issuer.example"})
    listed = Principal(subject="123-45-6789", issuer="https://issuer.example", roles=("reviewer",))
    assert approves(listed) is True

    # REJECTED: a colon-bearing (credential-shaped) subject, SAME clean https issuer.
    path.write_text("user:supersecret@https://issuer.example\n", encoding="utf-8")
    assert reviewer_allowlist() == frozenset()  # whole policy fails closed

    # And a Google-style numeric sub is fine too.
    path.write_text("1234567890@https://accounts.google.com\n", encoding="utf-8")
    assert reviewer_allowlist() == frozenset({"1234567890@https://accounts.google.com"})
