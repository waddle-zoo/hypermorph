"""An explicit, Git-owned approved-reviewer ALLOWLIST (hy-a607k, deferred slice B of
hy-mg8p).

A per-principal policy that gates the review surface and the review-authoring ops IN
ADDITION to the IdP-asserted `reviewer` role: the role says "this identity may review at
all", the allowlist says "and this SPECIFIC principal is an approved reviewer here". The
role half shipped in hy-mg8p; this is the explicit-allowlist half the overseer's
2026-08-09 "role/allowlist" ask named.

Git-owned BY REFERENCE, the same fail-closed-by-name pattern the write-back token and
other deployment config use: the operator commits the allowlist file to their own Git and
points `HYPERSET_REVIEWER_ALLOWLIST` at its path (e.g. a mounted config repo). Hyperset
reads the NAMES, never invents them, so the approved set is owned and versioned in the
customer's Git, not a Hyperset-side store. Each entry is one opaque `subject@issuer`
identity -- the same shape `operations._principal_identity` computes and the proposer/
assignee trails carry -- one per line; `#` comments and blank lines are ignored.

Default-OFF and fail-CLOSED:
- Env UNSET -> `reviewer_allowlist()` is `None` -> the allowlist is NOT enforced, so a
  deployment that has not opted in behaves exactly as before (role-only). This is also a
  no-op whenever the authz gate itself is off, because the callers check
  `authz_enabled()` first.
- Env SET but the file is missing/unreadable, or empty -> the approved set is EMPTY, so
  NOBODY network-facing is approved (deny wins on a misconfigured or empty policy).
- The trusted in-process `SYSTEM_PRINCIPAL` (eval/stdio) is always approved: it is not a
  network reviewer and the mandatory gate must never strand it -- the same exemption it
  has from the token role registry (hy-i4hc).

This is also the "known-principals registry" a later assign-to-ANOTHER-user slice
(hy-ip8do) validates an assignee against, instead of accepting a typed identity.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from hyperset.security.authz import SYSTEM_PRINCIPAL, Principal

ALLOWLIST_ENV = "HYPERSET_REVIEWER_ALLOWLIST"

# The canonical opaque `subject@issuer` grammar every allowlist ENTRY must match, so a
# malformed, credential-, or free-text-shaped line can never be inserted unchanged
# (adversary on #456; the class #455 fought). The subject is opaque and MUST NOT contain
# `@`, `/`, whitespace, or a COLON -- excluding `:` is what rejects a credential-shaped
# subject `user:supersecret@https://issuer.example` (adversary round 2: the `:` made
# `user:supersecret` a valid subject); the legit opaque-sub characters `| . _ + ~ -` are
# kept, so `auth0|abc123@...` and a purely numeric/hyphen IdP sub `123-45-6789@...` still
# pass. The issuer MUST be a clean `https://` URL with NO userinfo (its host class stops at
# any `@`, and its own `:` is only a port), so there is exactly one `@` and no
# `scheme://userinfo@` in a valid entry. This rejects `junk`, `user:supersecret@...`,
# `sub@https://user:tok@issuer.example` (issuer userinfo), and `https://user:secret@host`
# (no leading `subject@https://`). Anchored full-match.
#
# DELIBERATE BOUNDARY (mayor ruling, #456): a purely numeric/hyphen subject like
# `123-45-6789@https://issuer.example` IS accepted. The system does not judge an IdP's
# opaque subject as PII -- it accepts whatever the IdP asserts (the #455 ruling) -- and
# this validates the FILE's well-formedness only; the match at the gate is still against
# the VERIFIED principal's own identity, so a well-formed entry approves nobody whose
# IdP-verified `subject@issuer` it does not equal.
_IDENTITY_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._+~|-]*"  # opaque subject: no @, no /, no whitespace, NO colon
    r"@"
    r"https://[A-Za-z0-9.-]+(?::[0-9]+)?(?:/[A-Za-z0-9._~%-]*)*"  # https issuer, no userinfo
)


def reviewer_allowlist() -> frozenset[str] | None:
    """The approved-reviewer identities (opaque `subject@issuer`), or `None` when the
    allowlist is not configured.

    `None` (not enforced, role-only) is returned ONLY when the env var is truly UNSET.
    Every other outcome fails CLOSED to an EMPTY set (approves nobody), never silently to
    role-only:
    - the env is PRESENT but blank/whitespace -- a misconfiguration, not "unset";
    - the file is missing, unreadable, or NOT valid UTF-8 (a `UnicodeDecodeError` must not
      escape the authz decision as a 500);
    - ANY entry fails the canonical `subject@issuer` grammar -- one malformed/PII/credential
      line poisons the WHOLE policy, which is refused rather than partially trusted.

    Read fresh each call so an operator's edit takes effect without a restart; the file is
    small and OS-cached."""
    if ALLOWLIST_ENV not in os.environ:
        return None
    path = os.environ[ALLOWLIST_ENV].strip()
    if not path:
        # SET but blank/whitespace: the operator meant to enforce and misconfigured it.
        return frozenset()
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # Missing/unreadable, or malformed bytes -- the policy cannot be trusted.
        return frozenset()
    entries: set[str] = set()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if not _IDENTITY_RE.fullmatch(line):
            # A single malformed entry fails the WHOLE policy closed -- never insert it,
            # and never approve a subset off a policy that is partly garbage.
            return frozenset()
        entries.add(line)
    return frozenset(entries)


def approves(principal: Principal | None) -> bool:
    """Whether the approved-reviewer allowlist admits this principal for the REVIEW
    action. `True` when the allowlist is not configured (role-only, unchanged) or the
    caller's opaque `subject@issuer` is listed; the trusted in-process identity is always
    admitted. A configured allowlist denies an unlisted or unauthenticated caller
    (fail-closed) -- this is an ADDITIONAL necessary condition, ANDed with the reviewer
    role at the gate, never a grant on its own."""
    allow = reviewer_allowlist()
    if allow is None:
        return True
    if principal is SYSTEM_PRINCIPAL:
        return True
    if principal is None:
        return False
    return f"{principal.subject}@{principal.issuer}" in allow
