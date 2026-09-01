"""The canonical credential redactor (hq-hnrf #443 round 2).

ONE userinfo class `[^/]*@` for every boundary. The regression that mattered: a `?` or
`#` INSIDE the userinfo must still be stripped -- a `[^/?#]*@` class left those tokens
unredacted (hq-kbcy round 5, #431 round 8, #443 round 2).
"""

from __future__ import annotations

import pytest

from hyperset.security.redaction import redact_free_text_userinfo, redact_pointer

SECRET = "s3cr3ttoken"


@pytest.mark.parametrize(
    "pointer",
    [
        f"https://git-user:{SECRET}@host/repo.git",  # ordinary
        f"https://git-user:{SECRET}?x=1@host/repo.git",  # `?` inside userinfo
        f"https://git-user:{SECRET}#frag@host/repo.git",  # `#` inside userinfo
        f"https://git-user:{SECRET}@evil@host/repo.git",  # extra `@` -> LAST wins
        f"https://{SECRET}@host:99999/repo",  # malformed port (urlsplit would raise)
    ],
)
def test_redact_pointer_strips_every_userinfo_shape(pointer):
    out = redact_pointer(pointer)
    assert SECRET not in out, out
    assert out.startswith("https://host") or out.startswith("https://host:99999")


def test_redact_pointer_preserves_non_credential_values():
    # No userinfo before the first `/`: nothing to strip.
    assert redact_pointer("https://host/org/repo.git") == "https://host/org/repo.git"
    assert redact_pointer("/srv/local/repo") == "/srv/local/repo"
    assert redact_pointer("git@host:org/repo.git") == "git@host:org/repo.git"  # scp, no scheme
    # An `@` in the PATH is not userinfo and is preserved.
    assert redact_pointer("https://host/a@b") == "https://host/a@b"


def test_redact_free_text_strips_userinfo_anywhere_including_query_and_fragment():
    msg = f"clone failed for https://git-user:{SECRET}?x@host/repo.git at HEAD@{{1}}"
    out = redact_free_text_userinfo(msg)
    assert SECRET not in out and "git-user:" not in out
    # A revision like HEAD@{1} (its `@` is after a `/`) is preserved.
    assert "HEAD@{1}" in out
    assert redact_free_text_userinfo(None) is None
