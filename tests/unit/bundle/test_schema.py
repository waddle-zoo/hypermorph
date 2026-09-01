"""The public response shape's own guarantees (hy-gh-31).

Supplemental to `tests/postgres/test_context_bundle.py`, which resolves a
real bundle from a real Git commit and real pinned-source evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hyperset.bundle import (
    RETRYABLE_WARNING_CODES,
    WARNING_CODES,
    ContextBundle,
)
from hyperset.context.schema import (
    REF_AMBIGUOUS,
    REF_AWAITING_SYNC,
    REF_MALFORMED,
    REF_NOT_OBSERVED,
)


def _bundle(**overrides) -> ContextBundle:
    payload = {
        "request": {
            "query": "recognized revenue by region",
            "directive": {"domains": ["revenue"]},
        },
        "resolution": {"status": "governed", "summary": "", "warnings": []},
        "context_authority": {"type": "git", "commit_sha": "abc123"},
        "instructions": {"grain": "order_date by region"},
        "linked_evidence": {"observed_assets": [], "findings": []},
        "domain_graph": {"nodes": [], "edges": []},
        "provenance_refs": ["git_context:ctxsnap-1@abc123"],
        "resolved_at": datetime(2026, 7, 28, tzinfo=UTC),
    }
    payload.update(overrides)
    return ContextBundle(**payload)


def test_the_bundle_id_ignores_when_it_was_resolved():
    """Determinism is the contract: the same pinned context and source state
    is the same answer, whatever the clock says."""
    later = _bundle(resolved_at=datetime(2027, 1, 1, tzinfo=UTC))

    assert _bundle().bundle_id == later.bundle_id
    assert _bundle().to_dict()["resolved_at"] != later.to_dict()["resolved_at"]


def test_the_bundle_id_changes_when_any_evidence_changes():
    moved = _bundle(context_authority={"type": "git", "commit_sha": "def456"})

    assert moved.bundle_id != _bundle().bundle_id


# --- The multi-domain `domains[]` envelope (#230 slice 3, hy-cnto) ---


def _envelope(**overrides) -> ContextBundle:
    payload = {
        "request": {"query": "q", "directive": {"domains": ["a", "b"]}},
        "resolution": {"status": "governed", "summary": "", "warnings": []},
        "context_authority": None,
        "instructions": {},
        "linked_evidence": {"observed_assets": [], "findings": []},
        "domain_graph": {"nodes": [], "edges": []},
        "provenance_refs": [],
        "resolved_at": datetime(2026, 7, 28, tzinfo=UTC),
        "domains": [
            {"context_authority": {"commit_sha": "a1"}},
            {"context_authority": {"commit_sha": "b2"}},
        ],
    }
    payload.update(overrides)
    return ContextBundle(**payload)


def test_a_single_domain_bundle_carries_no_domains_key_and_is_byte_identical():
    # Additivity: with no `domains`, the served payload has no such key -- byte-for-byte
    # what it was before the field existed, so bundle_id is unmoved.
    assert _bundle().domains is None
    assert "domains" not in _bundle().to_dict()
    assert "domains" not in _bundle()._content()


def test_a_multi_domain_envelope_serves_the_domains_list_and_is_covered_by_bundle_id():
    envelope = _envelope()
    assert envelope.to_dict()["domains"] == envelope.domains
    # `domains` is GOVERNED content: it IS in the identity hash (unlike `assist`).
    assert "domains" in envelope._content()
    moved = _envelope(domains=[{"context_authority": {"commit_sha": "a1"}}])
    assert moved.bundle_id != envelope.bundle_id


@pytest.mark.parametrize(
    "bad",
    [
        {"context_authority": {"type": "git", "commit_sha": "x"}},
        {"instructions": {"grain": "day"}},
        {"provenance_refs": ["git_context:x@y"]},
        {"domain_graph": {"nodes": [{"id": "domain:a"}], "edges": []}},
        {"linked_evidence": {"observed_assets": [{"ref": "x"}], "findings": []}},
    ],
)
def test_a_multi_domain_bundle_refuses_any_nonempty_flat_governed_field(bad):
    # Fork-1 guardrail: `domains[]` present MUST mean the flat governed envelope is
    # empty and context_authority null, so it can never read as a single-authority
    # governed answer. A non-empty flat field fails construction, fail-closed.
    with pytest.raises(ValueError):
        _envelope(**bad)


# --- The composed cross-domain graph (#230 slice 5, hy-uaks) ---


def _composed(**overrides) -> ContextBundle:
    graph = {
        "nodes": [
            {"id": "domain:a", "kind": "domain", "label": "a"},
            {"id": "domain:b", "kind": "domain", "label": "b"},
        ],
        "edges": [
            {"from": "domain:a", "to": "domain:b", "relation": "contains", "evidence": "git"}
        ],
    }
    overrides.setdefault("composition", {"graph": graph})
    return _envelope(**overrides)


def test_a_single_domain_bundle_carries_no_composition_key():
    assert _bundle().composition is None
    assert "composition" not in _bundle().to_dict()
    assert "composition" not in _bundle()._content()


def test_a_composed_bundle_serves_composition_and_is_covered_by_bundle_id():
    composed = _composed()
    assert composed.to_dict()["composition"]["graph"]["edges"][0]["evidence"] == "git"
    assert "composition" in composed._content()  # governed content, in the identity hash
    # The flat envelope stays empty/null (the slice-3 guard is unrelaxed).
    assert composed.to_dict()["domain_graph"] == {"nodes": [], "edges": []}
    assert composed.to_dict()["context_authority"] is None


def test_composition_requires_domains():
    # The composed graph is only valid on a multi-domain answer.
    with pytest.raises(ValueError):
        _bundle(composition={"graph": {"nodes": [], "edges": []}})


def test_composition_graph_must_be_domain_level_only():
    # A within-domain NODE in the composed graph would flatten authorities -- refused.
    bad_node = {"graph": {"nodes": [{"id": "field:x", "kind": "field"}], "edges": []}}
    with pytest.raises(ValueError):
        _composed(composition=bad_node)


def test_composition_graph_edge_must_connect_two_composed_domain_nodes():
    # A within-domain EDGE endpoint (field:/source:) flattens content -- refused, even
    # when the nodes look fine. The guard covers edges, not only nodes.
    bad_edge = {
        "graph": {
            "nodes": [{"id": "domain:a", "kind": "domain", "label": "a"}],
            "edges": [
                {
                    "from": "field:secret",
                    "to": "source:leak",
                    "relation": "reads",
                    "evidence": "git",
                }
            ],
        }
    }
    with pytest.raises(ValueError):
        _composed(composition=bad_edge)


def test_composition_graph_refuses_a_governed_non_contains_edge():
    # THIS SLICE is contains-only: a Git-evidenced but non-`contains` relation is not
    # composed here until its emit lands (slice 2b). Fail-closed at the boundary.
    non_contains = {
        "graph": {
            "nodes": [
                {"id": "domain:a", "kind": "domain", "label": "a"},
                {"id": "domain:b", "kind": "domain", "label": "b"},
            ],
            "edges": [
                {"from": "domain:a", "to": "domain:b", "relation": "depends_on", "evidence": "git"}
            ],
        }
    }
    with pytest.raises(ValueError):
        _composed(composition=non_contains)


def test_composition_graph_refuses_a_domain_kind_node_with_a_non_domain_id():
    # A `kind: "domain"` label over a `field:`/`source:` id would still flatten content:
    # the id shape (`domain:{slug}`) is enforced, not only the kind.
    mislabelled = {"graph": {"nodes": [{"id": "field:x", "kind": "domain"}], "edges": []}}
    with pytest.raises(ValueError):
        _composed(composition=mislabelled)


def test_composition_graph_refuses_an_observed_edge():
    # The composed graph is GOVERNED; an observed edge belongs to a different graph.
    observed = {
        "graph": {
            "nodes": [
                {"id": "domain:a", "kind": "domain", "label": "a"},
                {"id": "domain:b", "kind": "domain", "label": "b"},
            ],
            "edges": [
                {
                    "from": "domain:a",
                    "to": "domain:b",
                    "relation": "lineage_to",
                    "evidence": "observed",
                }
            ],
        }
    }
    with pytest.raises(ValueError):
        _composed(composition=observed)


@pytest.mark.parametrize(
    "section",
    ["request", "resolution", "context_authority", "instructions", "linked_evidence"],
)
def test_every_contract_section_is_serialized(section):
    assert section in _bundle().to_dict()


def test_an_answer_with_no_assist_output_does_not_carry_the_key():
    """Not `null`, absent. ADR 0019 decision 2(b) promises that a caller
    reading the governed sections of an assisted answer gets exactly the
    governed answer it would have got with assist switched off -- byte for
    byte -- and an absent key is the cheapest way to make that checkable."""
    assert "assist" not in _bundle().to_dict()


def test_assist_output_is_served_beside_the_governed_answer_and_changes_none_of_it():
    assisted = _bundle(assist={"kind": "candidate_sources", "candidates": []})

    payload = assisted.to_dict()
    governed = _bundle().to_dict()

    assert payload["assist"] == {"kind": "candidate_sources", "candidates": []}
    assert {key: value for key, value in payload.items() if key != "assist"} == governed


def test_the_bundle_id_does_not_move_when_assist_output_does():
    """ADR 0019 floor 8: `bundle_id` hashes the governed answer alone. Assist
    need not be deterministic, and folding it into this hash would spend the
    determinism guarantee on the governed slice too -- caching, equality, and
    the recorded evaluation comparisons all read it."""
    assisted = _bundle(assist={"kind": "candidate_sources", "candidates": [{"rank": 1}]})
    differently = _bundle(assist={"kind": "candidate_sources", "candidates": [{"rank": 2}]})

    assert assisted.bundle_id == _bundle().bundle_id
    assert assisted.bundle_id == differently.bundle_id


def test_every_bundle_states_that_hyperset_ran_nothing():
    payload = _bundle().to_dict()

    assert payload["execution"] == {
        "performed_by_hyperset": False,
        "result_validated_by_hyperset": False,
    }
    assert payload["bundle_id"].startswith("cb-")


def test_a_bundle_carries_the_version_this_test_types_by_hand():
    """The number is written out here, and NOT imported from
    `hyperset.bundle`, for a reason worth stating once (hy-ndzz).

    An assertion about a constant must be able to go red on a CORRECT change
    to that constant. `payload["schema_version"] == SCHEMA_VERSION` -- what
    this line replaces -- compares the served value to the constant it was
    compiled from. It passes at 4, passes at 5, and passes at 41. Measured
    rather than argued: bumping `SCHEMA_VERSION` 4 -> 5 at f1cffe5 and running
    `tests/unit tests/integration` gave 660 passed, 0 failed.

    The sharper statement is that such a line cannot be wrong in EITHER
    direction. It survives the bump and it survives the un-bump, so no edit to
    the constant tells anyone anything by touching it. It is dead when it is
    written, not dead once the constant moves. The tempting repair for the red
    this test produces -- import the constant and compare it to itself -- is
    the bug, and it is a repair the repo has nearly made before.

    What this CANNOT do, stated so a later reader does not infer coverage that
    is not here: it catches a served version that disagrees with the number a
    human typed. It cannot catch a version that is wrong because the MEANING
    of an already-served value narrowed. Meaning is not in the payload and no
    assertion over the payload can reach it. ADR 0018 governs that question
    and a reviewer answers it.
    """
    assert _bundle().to_dict()["schema_version"] == 26


def test_every_retryable_code_is_a_warning_code():
    """The rule is a division of the disclosure vocabulary, not a vocabulary
    of its own (hy-amtg). Bound by a test rather than an import-time assert
    because `-O` strips asserts, and `warning()`'s gate in the same module is
    a real check -- two gates of different strength for no stated reason is
    how one of them quietly stops holding.
    """
    assert set(RETRYABLE_WARNING_CODES) <= set(WARNING_CODES)
    # And it is the split hy-6ae made, by what a caller must DO: edit a
    # malformed ref, qualify an ambiguous one, and neither for an absent one,
    # which needs the estate to change rather than the request.
    assert set(RETRYABLE_WARNING_CODES) == {REF_MALFORMED, REF_AMBIGUOUS}
    assert REF_NOT_OBSERVED not in RETRYABLE_WARNING_CODES
    # `ref_awaiting_sync` is the one a reader is most likely to add, because it
    # is the code that means "come back later" (hy-lcgq). Retryable means the
    # caller can fix it by asking again in the same session, and a connector
    # sync is not something asking again performs -- adding it here would
    # prescribe the loop
    # `test_re_sending_a_ref_after_ref_not_observed_is_the_retry_loop_the_prompt_forbids`
    # scores against, reached through a code that sounds encouraging. The new
    # code changes what an absence MEANS, not what fixes it. The prompt and
    # that scorer do not yet name it, which is hy-yk66 and needs a re-record.
    assert REF_AWAITING_SYNC not in RETRYABLE_WARNING_CODES


def test_domain_ambiguous_is_a_published_additive_warning_code():
    """The estate-ambiguity code (hy-gh-282), and the crux the panel verifies.

    It is published in `WARNING_CODES` (so `warning()` will emit it and the
    section-7 doc<->code gate enumerates it), and adding it is ADDITIVE: a new
    `resolution.warnings[].code` is an added value in a field that publishes
    default-deny, so it does NOT move `SCHEMA_VERSION` (ADR 0018 decision 5, the
    `ref_awaiting_sync` case). This is unlike hy-gh-285's `valid_with_gaps`, a
    new value in `status`, which publishes no default-deny and so DID move the
    number -- the distinction that makes this change SV-neutral.
    """
    from hyperset.bundle.schema import DOMAIN_AMBIGUOUS, SCHEMA_VERSION, WARNING_CODES, warning

    assert DOMAIN_AMBIGUOUS in WARNING_CODES
    assert warning(DOMAIN_AMBIGUOUS, "an estate ambiguity")["code"] == DOMAIN_AMBIGUOUS
    # SV-neutral: publishing the code did not move the served version. It has
    # since advanced (hy-gh-285's `valid_with_gaps` status to 9, hy-gh-287's eval
    # disclosure to 10), both new keys/values a caller RECEIVES with no
    # default-deny; a new additive warnings code was not among the moves.
    assert SCHEMA_VERSION == 26


def test_the_domain_collision_change_does_not_move_the_tools_hash():
    """The resolve-path planner tools hash a committed benchmark recording is
    pinned to is unaffected by THIS change: `domain_ambiguous` is resolve OUTPUT
    and disable/enable are CLI, so no CATALOG/RESOLVE description or input schema
    changed (hy-gh-282). The pinned value is fe930a003b731211 since hy-gh-281 item 3 added
    VALIDATE's input-schema field descriptions, which did move it; this one did
    not."""
    from hyperset.planner.loop import tools_hash

    assert tools_hash() == "sha256:fe930a003b731211"


def test_warning_message_redacts_a_credential_bearing_ref_at_the_server_boundary():
    # hy-icx1 #448 (the #447/#448 leak class): an evidence-ref warning interpolates
    # ref['ref'], and a ref carries an arbitrary external-id after its 3-part prefix,
    # so `superset:dataset:https://user:supersecret@gateway.example/v1` would put a
    # credential into the served warning message the chat UI renders verbatim. The
    # single factory every served warning flows through redacts URL userinfo, so no
    # consumer (MCP, HTTP, chat) receives it.
    from hyperset.bundle.schema import REF_NOT_OBSERVED, warning

    leaky = (
        "evidence ref 'superset:dataset:https://user:supersecret@gateway.example/v1' "
        "is not observed"
    )
    result = warning(REF_NOT_OBSERVED, leaky)
    assert "supersecret" not in result["message"]
    assert "user:supersecret@" not in result["message"]
    # The non-secret host stays, so the disclosure is still diagnosable.
    assert "gateway.example" in result["message"]


def test_a_clean_warning_message_is_byte_identical_so_bundle_hashes_do_not_move():
    # The redaction is a no-op on a message with no URL userinfo, so an ordinary
    # warning is byte-unchanged and the recorded bundle content hashes are untouched.
    from hyperset.bundle.schema import DOMAIN_AMBIGUOUS, warning

    clean = "the domain 'revenue' is claimed by more than one enabled source"
    assert warning(DOMAIN_AMBIGUOUS, clean)["message"] == clean
