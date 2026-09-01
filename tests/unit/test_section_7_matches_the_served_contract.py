"""`docs/v0-foundation.md` section 7 against the contract actually served.

CLAUDE.md calls that document binding, so it is authoritative and the tool
descriptions must match it: where the two disagree the doc wins and the
description is the bug.

Why this is a test rather than another `scripts/check_docs.py` section: that
script compares phrases to phrases and deliberately imports nothing from
`hyperset`, so it cannot in principle detect a document that contradicts the
code -- which is how section 7 named a parameter the server rejects, and
missed two response-shape changes, through four merged PRs while the gate
stayed green. Comparing a document to a contract means importing the contract.

The cost, so nobody is surprised by it: editing only the document can now fail
the test gate rather than the docs gate. That is the intended direction --
the doc is part of the contract, so it is checked against the contract.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from hyperset.bundle import (
    CUT,
    SCHEMA_VERSION,
    VIOLATION_CODES,
    WARNING_CODES,
    WITHHELD,
    ContextDirective,
)
from hyperset.bundle.discovery import PROPOSAL_OUTCOMES, PROPOSED, SIGNALS
from hyperset.bundle.equivalence import _QUOTES
from hyperset.bundle.reconcile import CONFLICT_PRODUCERS, RECONCILED_KINDS
from hyperset.processor import MOVED_SIDES
from hyperset.transport.operations import (
    _DIRECTIVE_KEYS,
    ERROR_CODES,
    HTTP_ERROR_CODES,
    OPERATION_ERROR_CODES,
    OPERATION_SPECS,
    OPERATIONS,
    UNKNOWN_OPERATION,
)

SECTION_7 = "## 7. Minimal v0 HTTP and MCP surface"
NEXT_SECTION = "## 8."
DOC = Path(__file__).resolve().parents[2] / "docs" / "v0-foundation.md"


# Spelled-out counts, so a sentence that lists three and says "four" fails.
_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


def _section_7() -> str:
    text = DOC.read_text()
    start = text.index(SECTION_7)
    return text[start : text.index(NEXT_SECTION, start)]


def _documented_signatures() -> dict[str, list[tuple[str, bool]]]:
    """Every `name(params) -> Result` line, with each parameter's optionality.

    A trailing `?` marks optional, matching how the section reads to a person.
    """
    signatures = {}
    for name, params in re.findall(r"`(\w+)\(([^)]*)\) -> \w+`", _section_7()):
        entries = [param.strip() for param in params.split(",") if param.strip()]
        signatures[name] = [(entry.rstrip("?"), entry.endswith("?")) for entry in entries]
    return signatures


def test_section_7_names_exactly_the_operations_that_are_served():
    """Both directions: a documented tool that does not exist misleads an
    agent into calling it, and a served tool the doc never mentions is a
    surface nobody reviewed."""
    assert set(_documented_signatures()) == set(OPERATIONS)


def _parameters_match(documented: list[str], served) -> bool:
    """Compare the accepted SET, which is the ruling this guard enforces.

    Sorted rather than `==` on lists: these are JSON object parameters, not
    positional arguments, so their order is presentational in the document and
    in `input_schema["properties"]` alike. Pinning the doc's reading order to
    dict insertion order in `operations.py` fails a documentation test for an
    edit that alters nothing an agent can observe, and a guard that reds on
    cosmetics is one somebody eventually disables rather than fixes (hy-at4).

    Sorted rather than `set`, because multiplicity is still a fact: a document
    naming the same parameter twice is a defect, and sets hide it.
    """
    return sorted(documented) == sorted(served)


def test_every_documented_parameter_is_one_the_server_accepts():
    """The original hy-698 defect, twice over: the doc named
    `include_observations`, which `operations.py` refuses as
    `unknown_parameter`, and omitted `limit`/`offset` after PR #77 added them.
    An agent following the binding document got an error."""
    for name, documented in _documented_signatures().items():
        served = OPERATION_SPECS[name]["input_schema"]["properties"]
        params = [param for param, _ in documented]
        # Spelled out rather than left to pytest: comparing through a helper
        # reports `assert False` and the whole served schema, where the two
        # sorted name lists are the entire finding.
        assert _parameters_match(params, served), (
            f"{name}: documented {sorted(params)}, served {sorted(served)}"
        )


def test_the_parameter_comparison_ignores_order_and_nothing_else():
    """What the loosening did and did not give up.

    Written against the comparison directly because the real signatures are
    correct: exercising it through the document would need a deliberately
    wrong document, and this states the intended tolerance in one place.
    """
    served = {"query": {}, "directive": {}}
    assert _parameters_match(["directive", "query"], served)
    assert not _parameters_match(["query"], served)
    assert not _parameters_match(["query", "directive", "include_observations"], served)
    assert not _parameters_match(["query", "query", "directive"], served)


def test_the_documented_optionality_matches_the_served_schema():
    """`bundle_id` read as optional in the doc while the server required it
    (hy-7ev). Optionality is the half of a signature an agent acts on."""
    for name, documented in _documented_signatures().items():
        required = set(OPERATION_SPECS[name]["input_schema"].get("required", []))
        assert {param for param, optional in documented if not optional} == required, name


def _documented_vocabulary(section: str) -> set[str]:
    """The codes section 7 lists, from the one sentence that enumerates them.

    Parsed from a delimited home rather than by collecting every backticked
    token, because section 7 legitimately backticks `bundle_id`, `asset_refs`,
    `max_hops`, `context_budget`, `next_offset` and `schema_version`. The
    obvious alternative -- intersect the backticked tokens with the vocabulary
    -- is a tautology: it discards exactly the tokens that would have failed
    the comparison, so a fabricated code passes.
    """
    sentence = re.search(r"The vocabulary is (.+?), exported as", section, re.S)
    assert sentence, "section 7 no longer enumerates the warning vocabulary"
    return set(re.findall(r"`([a-z_]+)`", sentence.group(1)))


def _documented_error_codes(section: str) -> tuple[set[str], set[str]]:
    """The two error vocabularies section 7 lists, each from its own sentence.

    Two sets rather than one, because the split is part of the contract: an
    MCP client can receive the first group and never the second, which are
    failures of the HTTP envelope before an operation is reached. A document
    that lists ten codes without saying which five an MCP client can get is
    not much better than one that lists none (hy-y633).
    """
    # Whitespace collapsed first: the sentences wrap, and a regex that depends
    # on where a line happens to break tests the formatter rather than the
    # contract.
    flat = " ".join(section.split())
    served = re.search(r"Either transport can return (.+?)\. `unknown_operation`", flat)
    envelope = re.search(r"An HTTP client can also receive (.+?), exported as", flat)
    assert served, "section 7 no longer enumerates the operation error vocabulary"
    assert envelope, "section 7 no longer enumerates the HTTP error vocabulary"
    return (
        set(re.findall(r"`([a-z_]+)`", served.group(1))),
        set(re.findall(r"`([a-z_]+)`", envelope.group(1))),
    )


def test_section_7_carries_the_whole_error_vocabulary_and_no_more():
    """Equality both ways, as for the warnings. These codes cross the wire
    through `OperationError.to_dict()`, so a code the document omits is a
    failure a client never learns to branch on -- and a code it promises that
    no wire delivers is the same defect pointing the other way.

    `unknown_operation` is the second kind. It is in the registry because
    `run_operation` raises it, and it is documented as in-process only because
    neither transport can deliver it: HTTP builds its routes from `OPERATIONS`
    so an unknown name is a 404 `unknown_route`, and MCP checks the tool name
    itself and answers JSON-RPC -32602. The first draft of this paragraph
    promised it to wire clients, which is why the sentence is split.
    """
    section = _section_7()
    served, envelope = _documented_error_codes(section)

    assert served == set(OPERATION_ERROR_CODES) - {UNKNOWN_OPERATION}
    assert served | {UNKNOWN_OPERATION} == set(OPERATION_ERROR_CODES)
    assert envelope == set(HTTP_ERROR_CODES)
    assert served | envelope | {UNKNOWN_OPERATION} == set(ERROR_CODES)
    # And it is documented in the role it actually has.
    flat = " ".join(section.split())
    assert "`unknown_operation` is raised by `run_operation` and reaches an in-process" in flat


def test_an_error_code_the_contract_does_not_have_fails_the_check():
    """The guard's own guard, the shape that caught the warning version: an
    invented code must move the documented set rather than being intersected
    away."""
    invented = _section_7().replace(
        "`internal_error`. `unknown_operation`",
        "`internal_error`, `teapot`. `unknown_operation`",
        1,
    )
    served, _ = _documented_error_codes(invented)

    assert "teapot" in served
    assert served != set(OPERATION_ERROR_CODES)


def _documented_truncation_reasons(section: str) -> set[str]:
    phrase = re.search(r"`reason` one of (.+?);", section, re.S)
    assert phrase, "section 7 no longer enumerates the truncation reasons"
    return set(re.findall(r"`([a-z_]+)`", phrase.group(1)))


def test_section_7_carries_the_whole_warning_vocabulary_and_no_more():
    """Equality, both directions. A code missing from the doc is a disclosure
    a planner never learns to handle -- what happened to `projection_bounded`,
    invisible to the inventory that missed it. A code the doc invents is a
    promise nothing keeps."""
    assert _documented_vocabulary(_section_7()) == set(WARNING_CODES)


def test_a_code_the_contract_does_not_have_fails_the_vocabulary_check():
    """The guard's own guard. The first version of the assertion above built
    its documented set by intersecting with `WARNING_CODES`, which holds for
    every possible document: a fabricated code injected into section 7 passed
    all six checks. This pins the direction that collapsed."""
    invented = _section_7().replace("exported as", "`ref_wrong_connector`, exported as", 1)

    assert _documented_vocabulary(invented) != set(WARNING_CODES)
    assert "ref_wrong_connector" in _documented_vocabulary(invented)


def _documented_violation_codes(section: str) -> set[str]:
    """The violation codes section 7 lists, from their own delimited sentence.

    Same shape as `_documented_vocabulary` and for the same reason: collecting
    every backticked token in the paragraph would sweep up `code`, `severity`,
    `section` and the operation names, and intersecting with the served tuple
    would be the tautology that let a fabricated warning code through.
    """
    sentence = re.search(r"The violation vocabulary is (.+?), exported as", section, re.S)
    assert sentence, "section 7 no longer enumerates the violation vocabulary"
    return set(re.findall(r"`([a-z_]+)`", sentence.group(1)))


def test_section_7_carries_the_whole_violation_vocabulary_and_no_more():
    """Equality, both directions, for the field the existing checks could not
    see (hy-ruui).

    `PlanValidation.violations[].code` is a served field like `warnings[].code`
    is, and section 7 named four of nineteen values in prose about something
    else while every mechanised check stayed green -- they enumerate
    `OperationError` codes and warning codes, which are different fields. That
    is a coverage boundary in the gate, not a regression, and this closes it by
    pointing the existing mechanism at one more field.
    """
    assert _documented_violation_codes(_section_7()) == set(VIOLATION_CODES)


def test_a_violation_code_the_validator_cannot_emit_fails_the_check():
    """The companion negative, copied from the warning pair: without it the
    assertion above can be satisfied by a document that lists a code nothing
    serves, which is a promise to a client that no plan will keep."""
    invented = _section_7().replace(
        "exported as `hyperset.bundle.VIOLATION_CODES`",
        "`teapot_source`, exported as `hyperset.bundle.VIOLATION_CODES`",
        1,
    )

    assert _documented_violation_codes(invented) != set(VIOLATION_CODES)
    assert "teapot_source" in _documented_violation_codes(invented)


def test_section_7_states_exactly_the_two_truncation_reasons():
    """Equality, not presence: `page.truncated`'s two reasons are the
    distinction #81 and #82 exist to make legible, and a third one documented
    against a contract that has two is the same defect as a fabricated code."""
    assert _documented_truncation_reasons(_section_7()) == {CUT, WITHHELD}


def test_the_announced_schema_version_is_the_one_the_server_serves():
    """The document and the constant, bound to each other (hy-1jxy).

    Every other check compares the served value to `SCHEMA_VERSION` itself --
    `tests/unit/bundle/test_schema.py` on the bundle payload, the health
    endpoint's response -- which proves the server serves the constant it was
    compiled with and passes at 4, at 5, and at 41. Those two become literals
    under hy-ndzz; this is the guard that does not need one, because it holds
    a record the constant cannot supply. Measured, not asserted: with the
    constant edited to 5 and the document left at 4, `tests/unit
    tests/integration` was 660 passed and `scripts/check_docs.py` exited 0.

    Two independent statements of one number, which is why this can fail: the
    document is written by a person applying ADR 0018 and the constant is read
    by the server, and neither is derived from the other. A bump that ships
    without its announcement fails here, and so does an announcement written
    for a bump nobody made.

    THE LIMIT, stated so no reader infers coverage that is not here: this
    catches a version that DISAGREES with its announcement. It cannot catch a
    version that is wrong because the MEANING of an existing served value
    narrowed -- meaning is not in the payload and no assertion over the payload
    reaches it. #165 came within one review of shipping exactly that with the
    gate green. That case stays human judgement; hy-87dn holds it.

    `scripts/check_docs.py` says in its own closing line that it "imports
    nothing from hyperset and cannot detect a document that contradicts the
    code", and names this file as the thing that does. For this field it did
    not, until now.
    """
    assert _documented_schema_version(_section_7()) == SCHEMA_VERSION


def test_a_document_that_announces_a_different_number_fails_the_check():
    """The companion negative every register here carries. Without it the
    assertion above is satisfied by a parser that finds nothing and quietly
    returns the constant -- the tautology this file was already written to
    avoid, in the one place where the parse is a single digit.

    Doctored from the number the document states rather than from the
    constant, so the substitution lands whether or not the two agree. Written
    the other way it is a no-op exactly when the guard above is red, which is
    the one run where a negative control has to still work.
    """
    announced = _documented_schema_version(_section_7())
    stale = _section_7().replace(
        f"**`SCHEMA_VERSION` is {announced}**", f"**`SCHEMA_VERSION` is {announced + 1}**"
    )

    assert _documented_schema_version(stale) == announced + 1
    assert _documented_schema_version(stale) != announced


def test_no_tool_description_claims_more_than_the_binding_document():
    """Two statements of one rule, which is hy-cy0's shape. The document is
    authoritative, so the assertion is one-directional by design: a
    description may say less than section 7, never more. A failure here means
    the description is the bug, not the doc."""
    section = _section_7()
    for name, spec in OPERATION_SPECS.items():
        described = set(re.findall(r"'([a-z_]+)'", spec["description"]))
        claimed = described & (set(WARNING_CODES) | set(OPERATIONS))
        assert claimed <= set(re.findall(r"`([a-z_]+)`", section)) | {name}, name


def test_the_document_names_every_assist_signal_the_ranking_actually_states():
    """`produced_by.signals` is served, so the document's list of it is checkable.

    In section 7's neighbour rather than section 7 itself -- the assist
    announcement sits in section 6 -- but it belongs in this file for this
    file's stated reason: comparing a document to a contract means importing the
    contract. `scripts/check_docs.py` passed unchanged while a fourth signal was
    added, because phrases-against-phrases cannot see a tuple (hy-g1y8).

    The count word is checked with the names. A doc that lists four signals and
    calls them three is wrong in the sentence a reader skims.

    What this does NOT bind, measured rather than assumed: renaming a signal in
    the enumerating sentence still passes when the real name survives elsewhere in
    the document -- `declared_references` appears twice, so mutating one
    occurrence leaves the other. It binds presence and the count, not position in
    a list. Pinning the sentence would mean re-deriving the doc's prose here,
    which is the phrases-against-phrases check this file exists to replace.
    """
    text = DOC.read_text()
    counts = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six"}

    for signal in SIGNALS:
        assert f"`{signal}`" in text, f"the document does not name the {signal!r} signal"
    assert f"{counts[len(SIGNALS)]} signals" in text, (
        f"the document must say {counts[len(SIGNALS)]!r} signals, not another number"
    )
    for wrong in (word for size, word in counts.items() if size != len(SIGNALS)):
        assert f"{wrong} signals" not in text, (
            f"the document still says {wrong!r} signals somewhere"
        )


def test_the_document_names_every_moved_side_a_finding_can_carry():
    """Same class as the signals guard above, for the same measured reason.

    The document claimed "which side moved" while nothing computed it, and every
    mechanised check stayed green, because the claim was prose and the answer was
    absent rather than misspelt (hy-qfyn). Binding the register to the document
    means a fifth value cannot be added without the sentence a reader skims
    moving with it.

    Also one-directional in the useful direction: it does not stop the document
    naming a word that is not in the register, which is deliberate -- the
    document names `neither` in order to say it is unreachable, and a guard that
    forbade that would forbid explaining an absence.
    """
    text = DOC.read_text()

    for side in MOVED_SIDES:
        assert f"`{side}`" in text, f"the document does not name the {side!r} moved side"
    assert "`hyperset.processor.MOVED_SIDES`" in text, (
        "the document must name the register, so a reader can find the published set"
    )


def test_the_document_names_every_producer_and_kind_a_conflict_can_carry():
    """`linked_evidence.conflicts` is mixed-provenance from SCHEMA_VERSION 7 on,
    and the label that makes it readable is only useful if its vocabulary is
    published. Same shape as the moved-side guard, one section further out: the
    registers are what a client branches on, so a third producer or a third
    reconciled kind cannot ship without the sentence a reader skims moving with
    it (hy-llk4).

    Deliberately not a count guard. The producers are a two-sided distinction --
    persisted finding against bundle-time computation -- and a third would change
    what the sentence says, not just its arithmetic, so a stale number is not the
    failure mode here.
    """
    text = DOC.read_text()

    for producer in CONFLICT_PRODUCERS:
        assert f"`{producer}`" in text, f"the document does not name the {producer!r} producer"
    for kind in RECONCILED_KINDS:
        assert f"`{kind}`" in text, f"the document does not name the {kind!r} reconciled kind"
    for register in ("CONFLICT_PRODUCERS", "RECONCILED_KINDS"):
        assert f"`hyperset.bundle.reconcile.{register}`" in text, (
            f"the document must name {register}, so a reader can find the published set"
        )


def _documented_directive_fields() -> list[str]:
    """The directive keys section 7 tells a caller it may SEND."""
    section = _section_7()
    marker = "`ContextDirective` is the planner's structured output"
    start = section.index(marker)
    listed = section[start : section.index(")", start)]
    return re.findall(r"`(\w+)`", listed[listed.index("(") :])


def _documented_service_only_directive_fields() -> list[str]:
    """Directive fields section 7 says exist but are NOT reachable over a transport."""
    section = _section_7()
    marker = "Not accepted on HTTP/MCP or the CLI yet:"
    start = section.index(marker)
    # Bounded to the bolded sentence, not the paragraph: the prose after it
    # names codes and test names in backticks too, and a parser that swept the
    # whole paragraph would quietly accept any of them as a directive field.
    return re.findall(r"`(\w+)`", section[start : section.index("**", start + len(marker))])


def test_the_documented_directive_fields_are_the_ones_a_transport_accepts():
    """hy-698 one level down, and the level nothing was watching (hy-c3dl).

    `test_every_documented_parameter_is_one_the_server_accepts` compares
    OPERATION parameters -- `query` and `directive` -- and never descends into
    the directive's own keys. So a field could be added to `ContextDirective`,
    documented in section 7, and refused by `run_operation` with a recovery
    telling the caller to delete the field the binding document told it to send,
    with 641 tests green. That is exactly what `assist` did.

    THREE lists, because two of them agreeing proves nothing about the third:
    what the document tells a caller to send, what the transport allow-list
    accepts, and what the dataclass actually carries.
    """
    documented = _documented_directive_fields()
    served = list(_DIRECTIVE_KEYS)
    carried = [entry.name for entry in dataclasses.fields(ContextDirective)]

    assert sorted(documented) == sorted(served), (
        f"documented {sorted(documented)}, accepted by the transports {sorted(served)}"
    )
    assert set(served) <= set(carried), (
        f"the transports accept {sorted(set(served) - set(carried))}, "
        "which the directive has no field for"
    )
    # A field the dataclass carries and no transport accepts is not a defect --
    # `assist` is exactly that until hy-hj9g's deliberate re-roll -- but it is
    # only honest if the document SAYS so, which is the third comparison.
    assert sorted(set(carried) - set(served)) == sorted(
        _documented_service_only_directive_fields()
    ), "every directive field a transport cannot accept must be named as such in section 7"


def test_the_document_names_every_proposal_outcome_the_assist_section_can_serve():
    """`proposal.outcome` is a value a client branches on, so the document's
    list of it is checkable against the tuple rather than against itself.

    In section 7's neighbour rather than section 7 itself -- the assist
    announcement sits in section 6 -- but it belongs in this file for this
    file's stated reason: comparing a document to a contract means importing
    the contract. `scripts/check_docs.py` compares phrases to phrases and
    cannot see a tuple, so it passed unchanged while the whole `proposal` key
    was added (hy-gh-124 slice 2).

    Read from the enumerating sentence and compared as a SET EQUALITY, not as a
    subset of every backtick in the file. Both directions matter and only the
    exact comparison gets the second one: an outcome the code serves and the
    document never names is a value nobody reviewed, and a documented outcome
    the code cannot produce is a branch a client writes and never reaches. A
    subset assertion over the whole document would also pass on a name that
    survives somewhere else in the file, which is the weakness the assist-signal
    binding measured and recorded (hy-g1y8).

    The count word is checked with the names, because a sentence that lists
    three and says "four" is wrong in the clause a reader skims.
    """
    sentence = re.search(
        r"a decline says which of (\w+) reasons withheld\s+it: (.+?)\.",
        DOC.read_text(),
        re.DOTALL,
    )
    assert sentence, "the enumerating sentence moved; this binding names it by its own words"
    declines = set(re.findall(r"`([a-z_]+)`", sentence.group(2)))

    assert declines == set(PROPOSAL_OUTCOMES) - {PROPOSED}
    assert sentence.group(1) == _WORDS[len(declines)]
    # `proposed` is not in that sentence -- it is the non-decline -- so it is
    # bound where the response shape is drawn instead.
    assert f"outcome: {PROPOSED}" in DOC.read_text()


def _documented_schema_version(section: str) -> int:
    """The version section 7 states it is served with.

    THE LIMIT BOTH VERSION READERS SHARE, stated here so nobody downstream
    reads a green suite as more than it is (hy-zn17). This takes the FIRST
    match of the bold claim inside section 7, and so does `_documented_moves`.
    The paragraph they read is itself a history paragraph that quotes past
    numbers, so a quoted "superseded" line sitting ABOVE a rolled-back real
    statement satisfies both while the statement a client would act on is
    wrong. Not contrived for that reason, and left standing rather than fixed
    because a reader who knows the limit is better served than one relying on
    a heuristic for which line is the real one.

    The wider limit, from the same review: these bind the NUMBERS a document
    names, never their meaning. Nothing mechanised checks that the number is
    the right one for the shape that changed. That rests on hq-17q6 and on
    review, and a green gate certifies neither.
    """
    stated = re.search(r"\*\*`SCHEMA_VERSION` is (\d+)\*\*", section)
    assert stated, "section 7 no longer states the version it is served with"
    return int(stated.group(1))


def _documented_moves(section: str) -> set[int]:
    """Every version the enumeration in that paragraph accounts for.

    Read from the sentence that lists them, not from the whole section, which
    also says "at 4" and "at 3" in the prose about merge-order allocation.

    The read stops at the first blank line, so EVERY entry has to live in that
    one sentence. A move written up as a separate paragraph below it reds this
    guard on a document that is complete and correct, which is a real trap at
    rebase time when two branches each add an entry their own way. Folding is
    the fix, and it is the right constraint to keep: one sentence is what makes
    a gap in the sequence visible to a person as well as to the regex.
    """
    sentence = re.search(r"\*\*`SCHEMA_VERSION` is \d+\*\*(.+?)\n\n", section, re.S)
    assert sentence, "section 7 no longer enumerates what each version moved for"
    return {int(number) for number in re.findall(r"\bat (\d+)\b", sentence.group(1))}


def test_the_version_history_accounts_for_every_number_up_to_the_current_one():
    """The paragraph does not just name the current version, it says what each
    move was for -- which is the part a client reads to decide whether an old
    integration still holds. A move announced by bumping the number and not the
    list leaves the newest shape the only undocumented one.

    Set equality, so it catches the other direction too: a history claiming a
    move at a version the code has not reached fails here as well as one
    missing an entry. That is stronger than "accounts for every number" reads,
    and it was confirmed by attack rather than asserted."""
    documented = _documented_moves(_section_7())

    # 1 is the initial shape and had no move to announce, so the history starts
    # at 2 -- the paragraph's own first entry.
    assert documented == set(range(2, SCHEMA_VERSION + 1))


def _documented_delimiters(section: str) -> set[str]:
    """The single-character delimiters section 7 names as held verbatim.

    Read from its own sentence, for the reason `_documented_vocabulary` gives:
    the paragraph legitimately backticks `'Completed'`, `[Region]` and the
    dollar-quoted forms, and intersecting with `_QUOTES` would be the tautology
    that let a fabricated warning code through.

    Two code-span forms, because a backtick delimiter can only be written as
    ``` `` ` `` ```. Matching them together with one loose pattern read the
    comma BETWEEN two spans as a delimiter; both forms require exactly one
    non-space character inside, which is also what excludes the dollar-quoted
    examples in the same sentence without naming them.
    """
    flat = " ".join(section.split())
    sentence = re.search(
        r"the delimiters are named rather than implied: (.+?)\. `'Completed'`", flat
    )
    assert sentence, "section 7 no longer names the delimiters the comparator holds"
    spans = re.findall(r"``\s*(\S)\s*``|`(\S)`", sentence.group(1))
    return {escaped or plain for escaped, plain in spans}


def test_section_7_names_exactly_the_delimiters_the_comparator_holds():
    """Equality, both directions, on the boundary between a value and a
    spelling. A delimiter the doc claims and the lexer does not hold is a
    promise that two different literals will not be folded together; a
    delimiter the lexer holds and the doc omits is a fold a plan author cannot
    predict. T-SQL's `[` is on neither side and section 7 now says so with the
    premise that makes it safe (hy-f37x).
    """
    assert _documented_delimiters(_section_7()) == set(_QUOTES)
    assert "[" not in _QUOTES


def test_section_7_carries_the_dialect_premise_and_the_trigger_that_reopens_it():
    """The premise was written down in one place a future editor would not be
    standing in -- the docstring of a single comparator test -- while section 7
    mentioned no dialect at all. It is load-bearing: it is the whole reason
    folding `[Region]` into `[region]` is right rather than a false pass, and
    it stops being true the day a second warehouse dialect lands.
    """
    flat = " ".join(_section_7().split())

    assert "Postgres is the only warehouse dialect Hyperset ships against" in flat
    assert "a second warehouse dialect reopens it" in flat
    # And the consequence a plan author can actually observe.
    assert "`[Region]` and `[region]` compare EQUIVALENT" in flat


def test_a_delimiter_the_lexer_does_not_hold_fails_the_check():
    """The guard's own guard, in the shape the warning and violation pairs use.
    The failure this pins is the one hy-f37x found: section 7 said "quoted
    identifiers are never folded" without naming which delimiters make an
    identifier quoted, and `[Region]` was folded under a sentence a reader
    would take as covering it.
    """
    # Injected into the FLATTENED text, which `_documented_delimiters` also
    # reads: the sentence wraps in the source, so a replacement anchored on a
    # phrase that straddles the line break silently does nothing and the
    # negative passes by having changed no document at all.
    flat = " ".join(_section_7().split())
    invented = flat.replace("and Postgres dollar-quoting", "`[`, and Postgres dollar-quoting", 1)
    assert invented != flat, "the anchor this negative injects at is gone"

    assert _documented_delimiters(invented) != set(_QUOTES)
    assert "[" in _documented_delimiters(invented)
