"""Canonical credential redaction for values that land in persisted/served text.

Pure and dependency-free (only `re`), so it is safe to reuse from any layer -- the
transport, the operations engine, the repositories -- without dragging a heavier import
into a purity-constrained module. The one job: strip URL userinfo (`user:token@`) from a
value before it is written to an audit detail, a served list, or a log line, so a token
that slipped into a stored pointer can never be surfaced.

ONE canonical userinfo class, `[^/]*@` -- it excludes ONLY `/`. A `?` or `#` can appear
INSIDE a crafted credential (`https://user:tok?@evil`, `https://user:tok#@evil`), so a
class that stopped at `?`/`#` (`[^/?#]*@`) would leave the token UNREDACTED (hq-kbcy
round 5; #431 round 8; hq-hnrf #443 round 2). The `@` must still be BEFORE the first `/`,
so a port + git revision (`https://git.corp:8443/team/repo:main HEAD@{1}`, whose `@` is
after the first `/`) and an scp ref (`git@host:path`, no scheme) are preserved. Backtracks
to the authority's LAST `@`, so extra `@` signs are captured too. Two compiled shapes off
the SAME class: anchored for a single pointer, global for a free-text field.
"""

from __future__ import annotations

import re

_SCHEME_USERINFO = r"([a-zA-Z][a-zA-Z0-9+.\-]*://)[^/]*@"

# Anchored: a single pointer value (a repository URL). Used to redact and to detect.
URL_USERINFO_RE = re.compile(r"^" + _SCHEME_USERINFO)
# Global/unanchored: a URL may appear MID-STRING in a free-text field (a git error).
URL_USERINFO_G = re.compile(_SCHEME_USERINFO)


def redact_pointer(value: str) -> str:
    """`value` (a single pointer, e.g. a repository URL) with any URL userinfo stripped,
    for defense-in-depth at a persist/serve boundary: `https://user:token@host/repo`
    becomes `https://host/repo`; anything without userinfo is returned unchanged.

    Purely TEXTUAL, so it NEVER raises and NEVER leaks: it does not call `urlsplit`, so a
    multi-`@` authority (no residual `evil@`) and a malformed port (whose
    `urlsplit(...).port` would raise) are both handled without an exception, and a `?`/`#`
    inside the userinfo is stripped rather than leaked."""
    return URL_USERINFO_RE.sub(r"\1", value.strip())


def redact_free_text_userinfo(value: str | None) -> str | None:
    """Redact every `scheme://userinfo@` appearing anywhere in a FREE-TEXT field (e.g. a
    git `last_error` that echoed the URL it failed on). Same canonical class as
    `redact_pointer`; purely textual, never raises, and matches nothing but real userinfo
    (a port + `HEAD@{1}`, an scp `git@host:path` are preserved)."""
    if not value:
        return value
    return URL_USERINFO_G.sub(r"\1", value)
