"""The merge precheck codifies "one merger is the control" (ADR 0014, hy-4xps).

The gh-driven `main`/`_fetch` are the thin edge; the logic under test is the pure
`parse_verdicts` reader (both spellings, hy-z2vh) and `evaluate` (the block rules
for hy-yq4i, hy-4xps/hy-o12b, hy-6hwt, plus the author/PR/full-sha binding an
adversarial review surfaced). Everything here runs with no network.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "merge_precheck", Path(__file__).resolve().parents[2] / "scripts" / "merge_precheck.py"
)
mp = importlib.util.module_from_spec(_SPEC)
# Register before exec so the frozen dataclass can resolve its own annotations
# (dataclasses looks the class's module up in sys.modules).
sys.modules["merge_precheck"] = mp
_SPEC.loader.exec_module(mp)

HEAD = "689bf470583958280d113057ae941088103c7107"
OTHER = "b" * 40
CRITIC = "critic-bot"
TRUSTED = {CRITIC}
GREEN = [
    {"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"},
    {"name": "migrations", "status": "COMPLETED", "conclusion": "SUCCESS"},
    {"name": "benchmark", "status": "COMPLETED", "conclusion": "SUCCESS"},
]

FORM_A = (
    f"HYPERSET-VERDICT v1 seat=hyperset/crew/critic pr=273 "
    f"sha={HEAD} verdict=MERGE conditional=none"
)
FORM_B = f"HYPERSET-VERDICT v1\n\n## VERDICT MERGE - PR #273 @ {HEAD}\n\nbody..."


def merge_verdict(body=FORM_B, author=CRITIC):
    return mp.parse_verdicts(body, author=author)


def ev(**kw):
    base = dict(
        head_sha=HEAD,
        base_sha="a" * 40,
        checks=GREEN,
        verdicts=merge_verdict(),
        pr_number="273",
        trusted_authors=TRUSTED,
    )
    base.update(kw)
    return mp.evaluate(**base)


# --- the reader: both spellings parse, neither is misread as absent (hy-z2vh) ---


def test_form_a_key_value_line_is_read():
    [v] = mp.parse_verdicts(FORM_A, author=CRITIC)
    assert (v.verdict, v.sha, v.pr, v.form) == ("MERGE", HEAD, "273", "A")


def test_form_b_markdown_heading_is_read():
    verdicts = mp.parse_verdicts(FORM_B, author=CRITIC)
    assert [(v.verdict, v.sha, v.pr) for v in verdicts] == [("MERGE", HEAD, "273")]


def test_a_bare_banner_with_no_ruling_yields_nothing():
    assert mp.parse_verdicts("HYPERSET-VERDICT v1\n\nstill deliberating") == []


def test_an_abbreviated_sha_names_no_head():  # hardened: full-oid only
    [v] = mp.parse_verdicts("## VERDICT MERGE - PR #1 @ 689bf47")
    assert v.sha is None
    assert mp._names_head(mp.Verdict("MERGE", "689bf47", "1", None, CRITIC, "B"), HEAD) is False


def test_full_oid_equality_names_the_head():
    assert mp._names_head(mp.Verdict("MERGE", HEAD, "1", None, CRITIC, "B"), HEAD) is True


# --- evaluate: the clean path clears ---


def test_a_clean_pr_is_clear_to_merge():
    assert ev() == []
    assert ev(verdicts=mp.parse_verdicts(FORM_A, author=CRITIC)) == []


# --- the block rules, incl. the adversarial false-clear directions ---


def test_head_equal_to_base_is_blocked_as_a_no_op():  # hy-yq4i
    assert any("no-op" in r for r in ev(base_sha=HEAD))


def test_a_pending_check_blocks():  # hy-4xps / hy-o12b
    checks = GREEN[1:] + [{"name": "test", "status": "IN_PROGRESS", "conclusion": None}]
    assert any("not completed" in r for r in ev(checks=checks))


def test_a_failing_check_blocks():
    checks = GREEN[1:] + [{"name": "test", "status": "COMPLETED", "conclusion": "FAILURE"}]
    assert any("not SUCCESS" in r for r in ev(checks=checks))


def test_a_required_check_that_never_ran_blocks():  # hy-o12b, review #4
    only_one = [{"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"}]
    reasons = ev(checks=only_one)
    assert any("migrations" in r and "did not run" in r for r in reasons)


def test_no_checks_at_all_blocks():
    assert any("no CI checks" in r for r in ev(checks=[]))


def test_a_check_with_missing_status_but_success_conclusion_blocks():  # review #5
    checks = GREEN[1:] + [{"name": "test", "conclusion": "SUCCESS"}]  # no status
    assert any("not completed" in r for r in ev(checks=checks))


def test_a_bounce_at_the_live_head_blocks_fail_safe():  # hy-6hwt
    v = mp.parse_verdicts(f"## VERDICT BOUNCE - PR #273 @ {HEAD}", author=CRITIC)
    assert any("fail safe" in r for r in ev(verdicts=v))


def test_a_bounce_beats_a_coexisting_stray_merge_at_head():  # review, fail-safe
    body = f"## VERDICT BOUNCE - PR #273 @ {HEAD}\n## VERDICT MERGE - PR #273 @ {HEAD}"
    assert any("fail safe" in r for r in ev(verdicts=mp.parse_verdicts(body, author=CRITIC)))


def test_a_merge_naming_a_stale_head_does_not_carry():  # hy-6hwt
    stale = mp.parse_verdicts(f"## VERDICT MERGE - PR #273 @ {OTHER}", author=CRITIC)
    assert any("does not carry" in r for r in ev(verdicts=stale))


def test_no_verdict_at_all_blocks():
    assert any("does not carry" in r for r in ev(verdicts=[]))


# --- authentication holes the adversarial review found ---


def test_a_verdict_from_an_untrusted_author_does_not_count():  # review #1 CRITICAL
    self_issued = mp.parse_verdicts(FORM_B, author="pr-author")
    assert any("does not carry" in r for r in ev(verdicts=self_issued))


def test_no_trusted_author_configured_honors_no_verdict():  # review #1
    reasons = ev(trusted_authors=None)
    assert any("no trusted reviewer identity configured" in r for r in reasons)


def test_a_verdict_naming_a_different_pr_does_not_carry():  # review #3
    other_pr = mp.parse_verdicts(f"## VERDICT MERGE - PR #999 @ {HEAD}", author=CRITIC)
    assert any("does not carry" in r for r in ev(verdicts=other_pr))


def test_a_fenced_or_quoted_verdict_is_not_a_ruling():  # 2nd-pass hole A
    fenced = f"Reminder, the format is:\n```\n{FORM_A}\n```"
    assert mp.parse_verdicts(fenced, author=CRITIC) == []
    quoted = f"> {FORM_A}"
    assert mp.parse_verdicts(quoted, author=CRITIC) == []
    inline = f"e.g. `## VERDICT MERGE - PR #273 @ {HEAD}`"
    assert mp.parse_verdicts(inline, author=CRITIC) == []


def test_a_verdict_without_a_pr_does_not_carry():  # 2nd-pass hole B
    no_pr = mp.parse_verdicts(f"## VERDICT MERGE @ {HEAD}", author=CRITIC)
    assert no_pr and no_pr[0].pr is None
    assert any("does not carry" in r for r in ev(verdicts=no_pr))


# --- Completes-Bead trailer as a merge precondition (hy-jasw) ---


def test_the_exact_completion_trailer_is_recognized():
    msgs = ["feat: do the thing\n\nbody\n\nCompletes-Bead: hy-jasw"]
    assert mp.has_completion_trailer(msgs, "hy-jasw") is True


def test_a_trailer_for_a_different_bead_is_not_proof():
    msgs = ["fix: x\n\nCompletes-Bead: hy-other"]
    assert mp.has_completion_trailer(msgs, "hy-jasw") is False


def test_the_bead_id_as_a_substring_is_not_an_exact_match():
    # A passing mention or a longer id must not satisfy the anchored predicate.
    msgs = ["Completes-Bead: hy-jasw-extra", "see Completes-Bead: hy-jasw in passing text"]
    assert mp.has_completion_trailer(msgs, "hy-jasw") is False


def test_a_missing_trailer_blocks_the_merge():  # hy-jasw: bead would land OPEN
    reasons = ev(bead_id="hy-jasw", commit_messages=["feat: no trailer here"])
    assert any("Completes-Bead: hy-jasw" in r and "land OPEN" in r for r in reasons)


def test_a_present_trailer_clears():
    assert ev(bead_id="hy-jasw", commit_messages=["feat: x\n\nCompletes-Bead: hy-jasw"]) == []


def test_no_bead_id_skips_the_trailer_check():
    # A non-bead PR (bead_id=None) is not held to a trailer.
    assert ev(bead_id=None, commit_messages=["feat: no trailer"]) == []


# --- the trailer requirement fires without the merger passing the id (hy-jasw) ---


@pytest.mark.parametrize(
    "branch,expected",
    [
        ("crew/hy-jasw-missing-trailer", "hy-jasw"),
        ("crew/hy-n9sq-facets-grain", "hy-n9sq"),
        ("crew/hy-gh-284-git-fetch-scope", "hy-gh-284"),  # the gh-mirrored id shape
        ("crew/hy-jasw", "hy-jasw"),  # no description slug
        ("crew/valkyrie/hy-80xq", "hy-80xq"),  # a nested (polecat) crew branch
        ("crew/hy-jasw-relates-hy-abcd", "hy-jasw"),  # the branch's OWN id, leftmost
        # bd short ids are NOT fixed-length: two- and three-char ids are common
        # (~15% of the ledger). A four-char assumption skipped their branches and
        # reopened the silent loss for them, so they are pinned here.
        ("crew/hy-2d2-catalog-bounds", "hy-2d2"),  # three-char id
        ("crew/hy-it-fix", "hy-it"),  # two-char id
        ("crew/hy-abcde-x", "hy-abcde"),  # five-char id, also matched
        ("crew/hy-x-tiny", "hy-x"),  # no length floor: even one char resolves
        ("crew/hy-ghx-fix", "hy-ghx"),  # a slug starting 'gh' is not a gh-<n> id
        # Not a bead: a pure-infra branch names none, so the PR is not held to a
        # trailer and this never false-fails.
        ("safety/completes-bead-trailer", None),
        ("chore/bump-deps", None),
        ("main", None),
        ("", None),
        (None, None),
        ("feature/adds-hy-jasw-support", None),  # id not at a path-segment start
        # hy-rd61: off-spec BOUNDARY / case a branch might carry must still resolve
        # to the bead it names, not silently drop it (the bead would land un-held).
        ("crew/hy-ab12_slug", "hy-ab12"),  # underscore terminates the id (was None)
        ("crew/hy-ab12.slug", "hy-ab12"),  # dot terminates the id (was None)
        ("crew/hy-AB12-slug", "hy-ab12"),  # upper/mixed case resolves + casefolds
        ("crew/HY-Ab12-slug", "hy-ab12"),  # case-insensitive prefix too
        # (b) A safety/chore branch whose segment reads as a bead id IS held -- an
        # OVER-require in the safe direction, stated precisely in the docstring.
        ("safety/hy-drate-config", "hy-drate"),
        # Don't over-widen: a segment that is not `hy-`-shaped still names no bead.
        ("crew/hyfoo-bar", None),  # no hyphen after 'hy', not an id
        ("safety/hydrate-config", None),  # 'hydrate' is not 'hy-...'
    ],
)
def test_the_branch_names_the_bead_or_names_none(branch, expected):
    assert mp.bead_id_from_branch(branch) == expected


def test_an_inferred_bead_with_no_trailer_blocks_and_a_present_one_clears():
    """The gap this closes (hy-jasw): the requirement must fire from the branch,
    so a merger who forgot the trailer -- and would also forget to pass the id --
    is still stopped. Composed exactly as `main` does: infer, then evaluate."""
    inferred = mp.bead_id_from_branch("crew/hy-jasw-missing-trailer")
    assert inferred == "hy-jasw"

    blocked = ev(bead_id=inferred, commit_messages=["feat: landed, no trailer"])
    assert any("Completes-Bead: hy-jasw" in r and "land OPEN" in r for r in blocked)

    assert ev(bead_id=inferred, commit_messages=["feat: x\n\nCompletes-Bead: hy-jasw"]) == []


def test_a_non_bead_branch_is_not_held_to_a_trailer():
    """The not-required arm end-to-end: a pure-infra branch infers no bead, so a
    trailerless PR clears (no false-fail)."""
    inferred = mp.bead_id_from_branch("safety/completes-bead-trailer")
    assert inferred is None
    assert ev(bead_id=inferred, commit_messages=["chore: no trailer, no bead"]) == []


# --- structured merge/close constraints, not prose (hy-sofx) ---


def _merge_v(constraints: str, *, verdict: str = "MERGE", author: str = CRITIC):
    """A counted verdict that names the live head and carries `constraints`."""
    return mp.Verdict(
        verdict, HEAD, "273", "hyperset/crew/critic", author, "A", mp.parse_constraints(constraints)
    )


def test_parse_constraints_reads_the_known_kinds_and_preserves_the_unreadable():
    parsed = mp.parse_constraints(
        "merge_after:#221 ; do_not_close:hy-z8dd ; weird ; bad: ; :orphan"
    )
    kinds = [(c.kind, c.ref, c.raw) for c in parsed]
    assert kinds[0] == ("merge_after", "#221", "merge_after:#221")
    assert kinds[1] == ("do_not_close", "hy-z8dd", "do_not_close:hy-z8dd")
    # A missing kind:ref shape is PRESERVED as unknown, never dropped.
    assert all(c.kind == "unknown" for c in parsed[2:])
    assert [c.raw for c in parsed[2:]] == ["weird", "bad:", ":orphan"]


@pytest.mark.parametrize("raw", ["none", "None", "  none  ", "", None])
def test_parse_constraints_treats_none_and_absent_as_no_constraint(raw):
    assert mp.parse_constraints(raw) == ()


def test_a_form_a_verdict_carries_its_constraints_field():
    body = (
        f"HYPERSET-VERDICT v1 seat=hyperset/crew/critic pr=273 sha={HEAD} "
        "verdict=MERGE constraints=do_not_close:hy-z8dd"
    )
    (verdict,) = [v for v in mp.parse_verdicts(body, author=CRITIC) if v.form == "A"]
    assert verdict.constraints == (
        mp.Constraint("do_not_close", "hy-z8dd", "do_not_close:hy-z8dd"),
    )


@pytest.mark.parametrize(
    "value",
    [
        "merge_after:#221;do_not_close:hy-z8dd",  # the emitted whitespace-free form
        "merge_after:#221 ; do_not_close:hy-z8dd",  # a human's spaces around ';'
    ],
)
def test_a_multi_constraint_field_is_read_whole_off_the_line_not_truncated(value):
    r"""The WIRE path (parse_verdicts' `constraints=` capture), where a naive
    `\S+` dropped every constraint after the first space -- the exact silent pass
    this feature removes. The whole ';'-run is captured, stopping before the next
    field."""
    body = (
        f"HYPERSET-VERDICT v1 seat=s pr=273 sha={HEAD} "
        f"verdict=MERGE constraints={value} conditional=none"
    )
    (verdict,) = [v for v in mp.parse_verdicts(body, author=CRITIC) if v.form == "A"]
    assert [(c.kind, c.ref) for c in verdict.constraints] == [
        ("merge_after", "#221"),
        ("do_not_close", "hy-z8dd"),
    ]


def test_a_bare_space_separated_second_constraint_does_not_vanish():
    r"""Shape E: two `kind:ref` tokens separated by a BARE SPACE, no ';'. A
    positional `\S+(?:\s*;\s*\S+)*` run stops at the space, so `merge_after:#221`
    -- a MERGE-blocker -- would drop off the line. A known blocker is
    self-identifying, so the whole-line scan catches it wherever it sits: it is
    read as a real `merge_after` and blocks until #221 lands, not silently
    passed."""
    body = (
        f"HYPERSET-VERDICT v1 seat=s pr=273 sha={HEAD} "
        "verdict=MERGE constraints=do_not_close:hy-z8dd merge_after:#221"
    )
    (verdict,) = [v for v in mp.parse_verdicts(body, author=CRITIC) if v.form == "A"]
    assert [(c.kind, c.ref) for c in verdict.constraints] == [
        ("do_not_close", "hy-z8dd"),
        ("merge_after", "#221"),
    ]
    # The merge-blocker did not vanish: it blocks until #221 lands, and clears
    # only when the forge reports it merged (so it is a real merge_after, not an
    # opaque unknown).
    assert any("until #221 lands" in r for r in ev(verdicts=[verdict]))
    assert (
        ev(
            verdicts=[verdict],
            merged_prs={"221"},
            acknowledged_constraints={"do_not_close:hy-z8dd"},
        )
        == []
    )


def test_a_space_after_the_equals_does_not_drop_the_field():
    """A human writes `constraints= merge_after:#221` (a space after the `=`). A
    capture that demanded `\\S` right after `=` would match nothing and drop the
    whole field. The whole-line scan finds the self-identifying blocker anyway,
    so it blocks -- not a silent pass."""
    body = (
        f"HYPERSET-VERDICT v1 seat=s pr=273 sha={HEAD} verdict=MERGE constraints= merge_after:#221"
    )
    (verdict,) = [v for v in mp.parse_verdicts(body, author=CRITIC) if v.form == "A"]
    assert [(c.kind, c.ref) for c in verdict.constraints] == [("merge_after", "#221")]
    assert any("until #221 lands" in r for r in ev(verdicts=[verdict]))


def test_a_capitalised_kind_is_recognised_not_dropped():
    """`Merge_after:#221` -- the kind capitalised -- must be read as the
    merge-blocker it is, not treated as prose and dropped. The scan is
    case-insensitive."""
    body = (
        f"HYPERSET-VERDICT v1 seat=s pr=273 sha={HEAD} "
        "verdict=MERGE constraints=none Merge_after:#221"
    )
    (verdict,) = [v for v in mp.parse_verdicts(body, author=CRITIC) if v.form == "A"]
    assert [(c.kind, c.ref) for c in verdict.constraints] == [("merge_after", "#221")]
    assert any("until #221 lands" in r for r in ev(verdicts=[verdict]))


def test_a_known_blocker_after_another_field_is_still_read():
    """A bare-space blocker that falls AFTER another `key=value` field
    (`conditional=none merge_after:#221`) is not lost to an `=`-stop: the
    whole-line scan is positionless, so it blocks."""
    body = (
        f"HYPERSET-VERDICT v1 seat=s pr=273 sha={HEAD} "
        "verdict=MERGE conditional=none merge_after:#221"
    )
    (verdict,) = [v for v in mp.parse_verdicts(body, author=CRITIC) if v.form == "A"]
    assert [(c.kind, c.ref) for c in verdict.constraints] == [("merge_after", "#221")]
    assert any("until #221 lands" in r for r in ev(verdicts=[verdict]))


def test_line_constraints_leaves_a_trailing_prose_note_as_prose():
    r"""A critic's trailing words that name no known kind (prose, a URL) are NOT
    turned into blocking constraints, or every note would false-block. Only a
    self-identifying `merge_after:`/`do_not_close:` token is caught; ordinary
    text after the field, including a colon-bearing word or a URL, is left
    alone."""
    body = (
        f"HYPERSET-VERDICT v1 seat=s pr=273 sha={HEAD} "
        "verdict=MERGE constraints=do_not_close:hy-z8dd see notes:below and https://x/y"
    )
    (verdict,) = [v for v in mp.parse_verdicts(body, author=CRITIC) if v.form == "A"]
    assert [(c.kind, c.raw) for c in verdict.constraints] == [
        ("do_not_close", "do_not_close:hy-z8dd"),
    ]


def test_a_known_blocker_on_a_form_b_heading_is_read():
    """Form B parity: a `constraints=` blocker on the `## VERDICT` heading line
    reaches the same reader as Form A, so neither spelling is a way to smuggle a
    merge past its condition."""
    body = f"## VERDICT MERGE - PR #273 @ {HEAD} constraints=merge_after:#221"
    (verdict,) = [v for v in mp.parse_verdicts(body, author=CRITIC) if v.form == "B"]
    assert [(c.kind, c.ref) for c in verdict.constraints] == [("merge_after", "#221")]
    assert any("until #221 lands" in r for r in ev(verdicts=[verdict]))


def test_a_comma_joined_second_kind_is_not_buried_in_the_first_refs():
    """A comma between two kinds (`do_not_close:hy-z8dd,merge_after:#221`) must not
    let the `merge_after` be swallowed into the `do_not_close` ref -- were it, a
    merger acking the printed `do_not_close:...,merge_after:...` raw would clear
    the merge before #221 landed. Both are read; the merge_after blocks on its own
    until #221 lands even when the do_not_close is acknowledged."""
    body = (
        f"HYPERSET-VERDICT v1 seat=s pr=273 sha={HEAD} "
        "verdict=MERGE constraints=do_not_close:hy-z8dd,merge_after:#221"
    )
    (verdict,) = [v for v in mp.parse_verdicts(body, author=CRITIC) if v.form == "A"]
    assert [(c.kind, c.ref) for c in verdict.constraints] == [
        ("do_not_close", "hy-z8dd"),
        ("merge_after", "#221"),
    ]
    # Acking only the do_not_close does NOT clear the un-landed merge_after.
    assert any(
        "until #221 lands" in r
        for r in ev(verdicts=[verdict], acknowledged_constraints={"do_not_close:hy-z8dd"})
    )


def test_a_constraint_inline_coded_on_an_operative_verdict_line_still_binds():
    """Inline code disarms a QUOTED banner (tested elsewhere), but it must not let
    a critic erase a constraint from an OPERATIVE verdict: the banner is read from
    the code-stripped line, yet constraints are read from the raw line, so a
    ``merge_after:#221`` in backticks on a real MERGE line still blocks."""
    body = (
        f"HYPERSET-VERDICT v1 seat=s pr=273 sha={HEAD} verdict=MERGE constraints=`merge_after:#221`"
    )
    (verdict,) = [v for v in mp.parse_verdicts(body, author=CRITIC) if v.form == "A"]
    assert verdict.verdict == "MERGE"  # the verdict itself is still operative
    assert any(c.kind == "merge_after" and c.ref.startswith("#221") for c in verdict.constraints)
    assert any("#221" in r for r in ev(verdicts=[verdict]))


@pytest.mark.parametrize("sep", ["|", "&", "/"])
def test_an_unenumerated_separator_cannot_bury_a_merge_after(sep):
    """The comma fix set a tight ref charset, so ANY separator this reader never
    enumerated -- `|`, `&`, `/` -- ends the ref rather than swallowing the next
    kind. The whole-line scan reads the `merge_after` on its own, so acking the
    printed buried raw (the routine do_not_close ack step) cannot clear the merge
    before #221 lands."""
    body = (
        f"HYPERSET-VERDICT v1 seat=s pr=273 sha={HEAD} "
        f"verdict=MERGE constraints=do_not_close:hy-z8dd{sep}merge_after:#221"
    )
    (verdict,) = [v for v in mp.parse_verdicts(body, author=CRITIC) if v.form == "A"]
    # The self-identifying merge_after is recovered as its own constraint.
    assert any(c.kind == "merge_after" and c.ref == "#221" for c in verdict.constraints)
    # Acking even the FULL buried raw does not clear the un-landed merge_after.
    acked = {c.raw for c in verdict.constraints if c.kind == "do_not_close"}
    reasons = ev(verdicts=[verdict], acknowledged_constraints=acked)
    assert any("until #221 lands" in r for r in reasons)


@pytest.mark.parametrize("glue", ["", "-", "_", "#", "x"])
def test_a_delimiterless_glue_of_a_second_kind_blocks_fail_safe(glue):
    """The residual of the tight ref charset: a ref-valid character (or NO
    delimiter) glues the next kind into the first ref
    (`do_not_close:hy-z8dd-merge_after:#221`), which a bead id may legitimately
    resemble, so the merge_after cannot be cleanly recovered. It must not pass:
    a ref that stops right before a ':' is a glued token read as `unknown`, so
    the verdict BLOCKS even when every surfaced constraint is acknowledged."""
    body = (
        f"HYPERSET-VERDICT v1 seat=s pr=273 sha={HEAD} "
        f"verdict=MERGE constraints=do_not_close:hy-z8dd{glue}merge_after:#221"
    )
    (verdict,) = [v for v in mp.parse_verdicts(body, author=CRITIC) if v.form == "A"]
    assert any(c.kind == "unknown" for c in verdict.constraints)
    # Acking everything surfaced still blocks -- an unknown constraint never clears.
    acked = {c.raw for c in verdict.constraints}
    assert ev(verdicts=[verdict], acknowledged_constraints=acked) != []


def test_a_triple_backtick_span_does_not_erase_a_constraint_off_an_operative_line():
    r"""A mid-line ``` ```merge_after:#221``` ``` span on a real MERGE line must not
    be pre-stripped as a fenced block: fence removal is line-anchored, so the span
    survives into the raw line and the blocker still blocks. A real fenced BLOCK
    (a fence marker starting a line) still disarms a quoted verdict entirely --
    tested by test_a_fenced_or_quoted_verdict_is_not_a_ruling."""
    fence = "`" * 3
    body = (
        f"HYPERSET-VERDICT v1 seat=s pr=273 sha={HEAD} "
        f"verdict=MERGE constraints={fence}merge_after:#221{fence}"
    )
    (verdict,) = [v for v in mp.parse_verdicts(body, author=CRITIC) if v.form == "A"]
    assert verdict.verdict == "MERGE"
    assert any(c.kind == "merge_after" and c.ref == "#221" for c in verdict.constraints)
    assert any("#221" in r for r in ev(verdicts=[verdict]))


def test_a_merge_after_constraint_blocks_until_the_referenced_pr_lands():
    unmet = ev(verdicts=[_merge_v("merge_after:#221")])
    assert any("until #221 lands" in r for r in unmet)
    # It clears exactly when the forge reports that PR merged.
    assert ev(verdicts=[_merge_v("merge_after:#221")], merged_prs={"221"}) == []
    # An unresolved reference is unmerged here -- fail toward blocking.
    assert any(
        "until #221 lands" in r
        for r in ev(verdicts=[_merge_v("merge_after:#221")], merged_prs=set())
    )


def test_a_do_not_close_constraint_blocks_until_the_merger_acknowledges_it():
    unacked = ev(verdicts=[_merge_v("do_not_close:hy-z8dd")])
    assert any("has not acknowledged" in r for r in unacked)
    # It clears only on an explicit acknowledgement of that exact token.
    assert (
        ev(
            verdicts=[_merge_v("do_not_close:hy-z8dd")],
            acknowledged_constraints={"do_not_close:hy-z8dd"},
        )
        == []
    )
    # Acknowledging a DIFFERENT bead does not clear it.
    assert any(
        "has not acknowledged" in r
        for r in ev(
            verdicts=[_merge_v("do_not_close:hy-z8dd")],
            acknowledged_constraints={"do_not_close:hy-other"},
        )
    )


def test_an_unreadable_constraint_always_blocks_even_if_acknowledged():
    """Fail toward blocking: a kind the gate does not know cannot be honored, so
    no acknowledgement can clear it -- merging past it is the silent pass this
    field exists to remove."""
    blocked = ev(
        verdicts=[_merge_v("frobnicate:hy-z8dd")],
        acknowledged_constraints={"frobnicate:hy-z8dd"},
    )
    assert any("cannot read" in r for r in blocked)


def test_a_verdict_with_no_constraints_clears():
    assert ev(verdicts=[_merge_v("none")]) == []


def test_constraints_only_bind_the_counted_clearing_verdict():
    """A constraint on a verdict that does NOT count -- a different head, an
    untrusted author, a BOUNCE -- must not bind: the merge is cleared by the
    counted MERGE, and only ITS constraints are enforced."""
    counted = _merge_v("none")
    other_head = mp.Verdict(
        "MERGE", "f" * 40, "273", "s", CRITIC, "A", mp.parse_constraints("frobnicate:x")
    )
    untrusted = mp.Verdict(
        "MERGE", HEAD, "273", "s", "not-a-critic", "A", mp.parse_constraints("frobnicate:x")
    )
    assert ev(verdicts=[counted, other_head, untrusted]) == []


def test_merge_constraints_returns_only_the_counted_merge_verdicts_constraints():
    counted = _merge_v("do_not_close:hy-z8dd")
    other_head = mp.Verdict(
        "MERGE", "f" * 40, "273", "s", CRITIC, "A", mp.parse_constraints("merge_after:#9")
    )
    got = mp.merge_constraints(
        [counted, other_head], head_sha=HEAD, pr_number="273", trusted_authors=TRUSTED
    )
    assert got == (mp.Constraint("do_not_close", "hy-z8dd", "do_not_close:hy-z8dd"),)


@pytest.mark.parametrize(
    "raw",
    [
        "do_not_close:hy-z8dd , merge_after:#221",  # comma-separated
        "do_not_close:hy-z8dd ; merge_after:#221",  # the field's own ';' reused
    ],
)
def test_the_acknowledgement_set_reads_either_separator(monkeypatch, raw):
    monkeypatch.setenv(mp.MERGE_ACK_ENV, raw)
    assert mp._acknowledged_constraints() == {"do_not_close:hy-z8dd", "merge_after:#221"}


def test_no_acknowledgement_env_is_the_empty_set(monkeypatch):
    monkeypatch.delenv(mp.MERGE_ACK_ENV, raising=False)
    assert mp._acknowledged_constraints() == set()


def test_merged_prs_leaves_an_unresolvable_ref_absent_rather_than_crashing(monkeypatch):
    def boom(args):
        raise SystemExit("gh pr view failed")

    monkeypatch.setattr(mp, "_gh_json", boom)
    # An unresolvable merge_after ref is absent -> evaluate reads it as unmerged
    # and blocks, rather than the precheck aborting.
    assert mp._merged_prs(mp.parse_constraints("merge_after:#221")) == set()


def test_merged_prs_queries_only_merge_after_refs_and_omits_the_unresolved(monkeypatch):
    seen = []

    def fake_gh(args):
        seen.append(args)
        pr = args[2]
        return {"state": "MERGED" if pr == "221" else "OPEN"}

    monkeypatch.setattr(mp, "_gh_json", fake_gh)
    constraints = mp.parse_constraints("merge_after:#221;merge_after:#222;do_not_close:hy-z8dd")
    assert mp._merged_prs(constraints) == {"221"}
    # Only the two merge_after refs were queried; the do_not_close bead was not.
    assert [a[2] for a in seen] == ["221", "222"]


# --- the reviewed-tree pin and the autonomous authorizer selection (hy-8b6c) ---

_TREE = "1" * 40


def _describes(landing_tree):
    def describes(*, tree_id, base, head):
        return tree_id == landing_tree

    return describes


def test_a_form_a_verdict_reads_its_tree_pin():
    (v,) = mp.parse_verdicts(
        f"HYPERSET-VERDICT v1 pr=273 sha={HEAD} verdict=MERGE tree={_TREE}", author=CRITIC
    )
    assert v.tree == _TREE


def test_a_form_b_verdict_reads_its_tree_pin_and_still_names_the_head():
    (v,) = mp.parse_verdicts(f"## VERDICT MERGE - PR #273 @ {HEAD} tree={_TREE}", author=CRITIC)
    assert (v.sha, v.tree) == (HEAD, _TREE)


def test_a_tree_id_or_subtree_field_is_not_read_as_a_tree_pin():
    # The literal `tree=` must not be tripped by `tree_id=` (a gate line) or a
    # `subtree=`: the pin stays absent, so such a verdict authorizes no auto-land.
    (a,) = mp.parse_verdicts(
        f"HYPERSET-VERDICT v1 pr=273 sha={HEAD} verdict=MERGE tree_id={_TREE}", author=CRITIC
    )
    (b,) = mp.parse_verdicts(
        f"HYPERSET-VERDICT v1 pr=273 sha={HEAD} verdict=MERGE subtree={_TREE}", author=CRITIC
    )
    assert a.tree is None and b.tree is None


# One forge account (hy-irni): both roles post as the SAME trusted login, so
# distinctness is by ROLE, and the tests key trust off one login.
_BSOVS = "bsovs"


def test_a_form_a_verdict_reads_its_role_tag():
    (v,) = mp.parse_verdicts(
        f"HYPERSET-VERDICT v1 pr=273 sha={HEAD} verdict=MERGE role=critic", author=_BSOVS
    )
    assert v.role == "critic"


def test_a_form_b_verdict_reads_its_role_tag():
    (v,) = mp.parse_verdicts(f"## VERDICT MERGE - PR #273 @ {HEAD} role=adversary", author=_BSOVS)
    assert v.role == "adversary"


def test_a_myrole_field_is_not_read_as_a_role_tag():
    (v,) = mp.parse_verdicts(
        f"HYPERSET-VERDICT v1 pr=273 sha={HEAD} verdict=MERGE myrole=critic", author=_BSOVS
    )
    assert v.role is None


def _pinned(role, comment, *, author=_BSOVS, tree=_TREE):
    (v,) = mp.parse_verdicts(
        f"HYPERSET-VERDICT v1 pr=273 sha={HEAD} verdict=MERGE tree={tree} role={role}",
        author=author,
        comment=comment,
    )
    return v


def _authorizers(verdicts, *, describes=None, base_sha="a" * 40):
    return mp.merge_authorizers(
        verdicts,
        head_sha=HEAD,
        pr_number="273",
        trusted_authors={_BSOVS},
        base_sha=base_sha,
        describes=describes or _describes(_TREE),
    )


def test_merge_authorizers_counts_distinct_roles_from_one_login():
    # The hy-irni fix: two roles, ONE forge account, distinct comments -> two.
    verdicts = [_pinned("critic", "c1"), _pinned("adversary", "c2")]
    assert _authorizers(verdicts) == {"critic", "adversary"}


def test_two_verdicts_of_the_same_role_count_once():
    verdicts = [_pinned("critic", "c1"), _pinned("critic", "c2")]
    assert _authorizers(verdicts) == {"critic"}


def test_an_unknown_role_is_not_counted():
    verdicts = [_pinned("critic", "c1"), _pinned("reviewer", "c2")]
    assert _authorizers(verdicts) == {"critic"}


def test_a_verdict_with_no_role_tag_authorizes_nothing():
    (untagged,) = mp.parse_verdicts(
        f"HYPERSET-VERDICT v1 pr=273 sha={HEAD} verdict=MERGE tree={_TREE}",
        author=_BSOVS,
        comment="c1",
    )
    verdicts = [untagged, _pinned("adversary", "c2")]
    assert _authorizers(verdicts) == {"adversary"}


def test_one_comment_naming_both_roles_counts_for_none():
    # A single comment claiming both roles cannot stand in for two reviewers.
    verdicts = [_pinned("critic", "same"), _pinned("adversary", "same")]
    assert _authorizers(verdicts) == set()


def test_merge_authorizers_drops_a_role_whose_pinned_tree_is_no_longer_landing():
    # The pin no longer equals the would-land tree (a base advance changed it):
    # describes() is False, so neither role authorizes -- the #302 core still holds.
    verdicts = [_pinned("critic", "c1"), _pinned("adversary", "c2")]
    assert _authorizers(verdicts, describes=_describes("2" * 40)) == set()


def test_merge_authorizers_ignores_a_role_verdict_with_no_tree_pin():
    (unpinned,) = mp.parse_verdicts(
        f"HYPERSET-VERDICT v1 pr=273 sha={HEAD} verdict=MERGE role=critic",
        author=_BSOVS,
        comment="c1",
    )
    verdicts = [unpinned, _pinned("adversary", "c2")]
    assert _authorizers(verdicts) == {"adversary"}  # only the pinned role authorizes


def test_merge_authorizers_is_empty_without_a_base_to_compare():
    verdicts = [_pinned("critic", "c1"), _pinned("adversary", "c2")]
    assert _authorizers(verdicts, base_sha=None) == set()


# --- completion_beads: which bead(s) a merged PR's trailers name (hy-n0ge) ---


def test_completion_beads_reads_the_single_trailer():
    msgs = ["feat: x\n\nbody\n\nCompletes-Bead: hy-n0ge"]
    assert mp.completion_beads(msgs) == ["hy-n0ge"]


def test_completion_beads_is_empty_with_no_trailer():
    assert mp.completion_beads(["feat: no trailer here"]) == []
    assert mp.completion_beads([]) == []
    assert mp.completion_beads(None) == []


def test_completion_beads_returns_each_distinct_id_once_in_order():
    msgs = [
        "a\n\nCompletes-Bead: hy-aaa",
        "b\n\nCompletes-Bead: hy-bbb",
        "c\n\nCompletes-Bead: hy-aaa",  # a repeat
    ]
    assert mp.completion_beads(msgs) == ["hy-aaa", "hy-bbb"]


def test_completion_beads_is_anchored_a_mention_is_not_a_trailer():
    # A passing mention mid-line is not a Completes-Bead trailer (anchored ^...$),
    # so the reconciler never closes a bead named in prose.
    msgs = ["see Completes-Bead: hy-xxx in passing"]
    assert mp.completion_beads(msgs) == []


# --- hy-bclr: a voided verdict leaves a RESIDUE, asymmetric by disposition ---

THIRD = "c" * 40


def test_a_stale_bounce_marks_the_head_a_re_grade_not_a_first_review():
    # A BOUNCE at an earlier head, none at the live one: the rewrite voided the
    # rejection by the 40-hex rule. The block must SAY this head was rejected
    # before, so 'no live verdict' is not read as 'never reviewed'.
    stale = mp.parse_verdicts(f"## VERDICT BOUNCE - PR #273 @ {OTHER}", author=CRITIC)
    reasons = ev(verdicts=stale)
    assert any("RE-GRADE" in r and "fail OPEN" in r for r in reasons)


def test_a_stale_bounce_does_not_block_a_fresh_valid_merge():
    # FAIL OPEN: an old rejection must NOT veto a rewritten, re-graded head. The
    # prior BOUNCE at OTHER plus a fresh MERGE at the live head clears.
    fresh = mp.parse_verdicts(f"## VERDICT BOUNCE - PR #273 @ {OTHER}", author=CRITIC)
    fresh += mp.parse_verdicts(FORM_B, author=CRITIC)  # MERGE @ HEAD
    assert ev(verdicts=fresh) == []


def test_no_verdict_is_distinguishable_from_a_stale_bounce():
    # The core asymmetry the bead names: absence of a LIVE verdict must be
    # distinguishable from absence of ANY verdict. A never-graded PR carries no
    # re-grade residue; a bounced-then-rewritten one does.
    never = ev(verdicts=[])
    bounced = ev(
        verdicts=mp.parse_verdicts(f"## VERDICT BOUNCE - PR #273 @ {OTHER}", author=CRITIC)
    )
    assert not any("RE-GRADE" in r for r in never)
    assert any("RE-GRADE" in r for r in bounced)


def test_a_stale_merge_marks_the_head_a_re_gate():
    # The other arm: a stale MERGE fails safe on its own (it authorizes nothing),
    # and its residue names the re-gate need rather than a re-review.
    stale = mp.parse_verdicts(f"## VERDICT MERGE - PR #273 @ {OTHER}", author=CRITIC)
    reasons = ev(verdicts=stale)
    assert any("re-gate this head" in r and "fail SAFE" in r for r in reasons)


def test_a_stale_bounce_outweighs_a_stale_merge_in_the_residue():
    # If a head was both approved and rejected at earlier heads, the rejection is
    # the fact that must not go silent, so the BOUNCE residue is reported.
    both = mp.parse_verdicts(f"## VERDICT MERGE - PR #273 @ {OTHER}", author=CRITIC)
    both += mp.parse_verdicts(f"## VERDICT BOUNCE - PR #273 @ {THIRD}", author=CRITIC)
    assert any("RE-GRADE" in r for r in ev(verdicts=both))


def test_stale_verdicts_selects_only_trusted_this_pr_other_head():
    # An untrusted author, a different PR, and the live head are each excluded.
    stale = mp.parse_verdicts(f"## VERDICT BOUNCE - PR #273 @ {OTHER}", author=CRITIC)
    untrusted = mp.parse_verdicts(f"## VERDICT BOUNCE - PR #273 @ {THIRD}", author="rando")
    other_pr = mp.parse_verdicts(f"## VERDICT BOUNCE - PR #999 @ {THIRD}", author=CRITIC)
    at_head = mp.parse_verdicts(f"## VERDICT BOUNCE - PR #273 @ {HEAD}", author=CRITIC)
    picked = mp.stale_verdicts(
        stale + untrusted + other_pr + at_head,
        head_sha=HEAD,
        pr_number="273",
        trusted_authors=TRUSTED,
    )
    assert [v.sha for v in picked] == [OTHER]


def test_stale_residue_is_none_without_a_trusted_stale_verdict():
    assert mp.stale_residue([], head_sha=HEAD, pr_number="273", trusted_authors=TRUSTED) is None
