"""The discovery surface over the real slice (hy-x7f), GitHub #70.

A planner reads this before it names anything, so what matters is that every
line is a fact from the pinned commit or a count of live observations, and
that governed meaning is not among them: the catalog helps an agent choose
where to look, and the bundle is the only thing that says what is true.
"""

from __future__ import annotations

import pytest

from hyperset.bundle import (
    CATALOG_DEFAULT_LIMIT,
    CATALOG_INNER_LIMIT,
    CATALOG_MAX_LIMIT,
    CatalogBoundError,
    ContextDirective,
    list_context_catalog,
    resolve_analytics_context,
)
from hyperset.bundle.catalog import CUT, UNBOUNDED_LISTS, WITHHELD
from hyperset.bundle.resolver import domain_graph, git_instructions, projection_summary
from hyperset.repositories.postgres import PostgresContextRepository
from tests.postgres.conftest import (
    APPROVED_DATASET,
    sync_revenue_context,
)

APPROVED_REF = f"superset:dataset:{APPROVED_DATASET}"
APPROVED_SOURCE = "table:postgres:analytics.public.finance_orders_daily"


@pytest.mark.postgres
def test_the_catalog_lists_the_configured_domain_from_its_pinned_commit(
    session_factory, revenue_slice
):
    catalog = list_context_catalog(session_factory=session_factory)

    (revenue,) = catalog.domains
    assert revenue["domain"] == "revenue"
    assert revenue["context_authority"]["commit_sha"] == revenue_slice["context"].commit_sha
    assert revenue["context_authority"]["type"] == "git"
    assert revenue["owner_refs"] == ["team:finance-data"]


@pytest.mark.postgres
def test_the_catalog_names_the_concepts_and_refs_a_directive_can_ask_for(
    session_factory, revenue_slice
):
    (revenue,) = list_context_catalog(session_factory=session_factory).domains

    assert "recognized_revenue" in revenue["concepts"]
    assert APPROVED_SOURCE in revenue["approved_source_refs"]
    assert APPROVED_REF in revenue["evidence_refs"]
    assert revenue["prohibited_source_refs"]
    assert [document["kind"] for document in revenue["documents"]] == ["context_doc"]
    # Enough shape to plan a traversal without retrieving one.
    assert {"concept", "source", "field"} <= set(revenue["node_kinds"])
    assert {"defined_in", "approved_for", "reads"} <= set(revenue["relationships"])


@pytest.mark.postgres
def test_what_the_catalog_names_is_what_the_resolver_answers_for(session_factory, revenue_slice):
    """The discovery surface and the answer are the same stored context: a
    domain listed here resolves, and resolves against the commit listed."""
    (revenue,) = list_context_catalog(session_factory=session_factory).domains

    bundle = resolve_analytics_context(
        query="Which source and rules should an analyst use for recognized revenue by region?",
        directive=ContextDirective(
            domains=[revenue["domain"]],
            concepts=revenue["concepts"],
            asset_refs=[APPROVED_REF],
        ),
        session_factory=session_factory,
    )

    assert bundle.status == "governed"
    assert bundle.context_authority["commit_sha"] == revenue["context_authority"]["commit_sha"]
    assert [item["ref"] for item in bundle.linked_evidence["observed_assets"]] == [APPROVED_REF]


@pytest.mark.postgres
def test_counts_reports_the_refs_the_commit_declared_not_the_ones_it_serves(
    session_factory, tmp_path
):
    """hy-bcim. The list of seeds is filtered to what a connector observed --
    seeding with an uncorroborated ref returns nothing, so offering it would
    waste a call. But `counts` used to be computed from the filtered list, so
    it agreed with the omission and nothing in the response revealed that
    anything had been dropped: three declared refs and four with one
    uncorroborated were the same catalog.

    `counts` is the full size of a list everywhere else in this module, and it
    is the full size here too: what the commit declared. The gap between it
    and the served list is the disclosure.
    """
    sync_revenue_context(session_factory, tmp_path)

    (revenue,) = list_context_catalog(session_factory=session_factory).domains

    assert revenue["evidence_refs"] == []
    assert revenue["counts"]["evidence_refs"] == 3


@pytest.mark.postgres
def test_the_catalog_carries_no_meaning_only_identifiers(session_factory, revenue_slice):
    """It must not become a second, unprovenanced context surface: a
    definition's text, a field expression, a filter, or a caveat read here
    would carry no authority, no commit, and no warnings."""
    (revenue,) = list_context_catalog(session_factory=session_factory).domains

    listed = str(revenue)
    assert "SUM(gross_amount - tax_amount)" not in listed
    assert "status = 'completed'" not in listed
    for key in ("definitions", "fields", "filters", "joins", "grain", "caveats", "validations"):
        assert key not in revenue


@pytest.mark.postgres
def test_the_observed_side_is_counted_not_listed(session_factory, revenue_slice):
    """The catalog exists to avoid sending the corpus, so it says how much of
    each kind exists and nothing about any single asset."""
    catalog = list_context_catalog(session_factory=session_factory)

    datasets = next(
        entry
        for entry in catalog.observed
        if entry["connector"] == "superset" and entry["asset_type"] == "dataset"
    )
    assert datasets["live_count"] > 0
    assert "datahub" in {entry["connector"] for entry in catalog.observed}
    assert APPROVED_DATASET not in str(catalog.observed)


@pytest.mark.postgres
def test_the_catalog_is_serialized_whole(session_factory, revenue_slice):
    payload = list_context_catalog(session_factory=session_factory).to_dict()

    assert set(payload) == {"schema_version", "generated_at", "page", "domains", "observed"}
    assert payload["domains"][0]["domain"] == "revenue"
    # The VALUE, typed by hand rather than imported (hy-q4ln). The set
    # comparison above proves the key is served and holds at any number, which
    # is how the catalog reached four served surfaces with its version asserted
    # nowhere. Comparing to `SCHEMA_VERSION` would put the hy-ndzz tautology on
    # a third surface: the served value is that constant, so the two move
    # together and the assertion cannot fail on a wrong number. A literal is
    # the constant's value plus a human keystroke, and the keystroke is the
    # check -- it must be moved deliberately at every bump.
    assert payload["schema_version"] == 26


@pytest.mark.postgres
def test_a_complete_catalog_says_it_is_complete(session_factory, revenue_slice):
    page = list_context_catalog(session_factory=session_factory).page

    assert page == {
        "limit": CATALOG_DEFAULT_LIMIT,
        "offset": 0,
        "domain_count": 1,
        "unevaluated_domain_count": 0,
        "next_offset": None,
        "truncated": [],
        "recovery": None,
    }


@pytest.mark.postgres
def test_a_truncated_list_is_cut_positionally_and_says_so(session_factory, wide_context):
    """Truncation is position over the ordering the catalog already uses --
    never a judgement about which entries matter, which is the relevance
    logic GitHub #70 removed (hy-aq3).

    Run against a corpus that actually exceeds `INNER_LIMIT`: the checked-in
    fixture's lists are single digit, so at the shipped bound it proves the
    slicing expression and nothing about the bound (hy-5b1).
    """
    entries = wide_context["entries"]
    catalog = list_context_catalog(session_factory=session_factory)
    first = catalog.domains[0]

    assert entries > CATALOG_INNER_LIMIT
    assert first["concepts"] == sorted(first["concepts"])[:CATALOG_INNER_LIMIT]
    assert len(first["concepts"]) == CATALOG_INNER_LIMIT
    assert len(first["owner_refs"]) == CATALOG_INNER_LIMIT
    # Withheld, not cut: the one list whose only use is seeding a directive.
    # The count has to survive it -- omitting the list and the count together
    # would trade a partial list for silence, which is worse than the prose it
    # replaced, because the prose at least said the refs existed.
    assert "evidence_refs" not in first
    assert first["counts"]["evidence_refs"] == len(wide_context["terms"]) + len(
        wide_context["prohibited"]
    )
    # The omission is disclosed through `page.truncated` and `counts`, and
    # through nothing else. A per-domain marker key would be uncounted,
    # uncapped and outside the inventory the bound is computed over -- the
    # hy-c7f shape a third time.
    assert set(first) == {
        "domain",
        "title",
        "context_authority",
        "counts",
        "concepts",
        "documents",
        "approved_source_refs",
        "prohibited_source_refs",
        "owner_refs",
        "node_kinds",
        "relationships",
    }
    # What was cut, and how much of it there was -- for every list, including
    # the one that used to be served whole and uncounted (hy-c7f).
    domain = first["domain"]
    declared = {
        "concepts": entries,
        "owner_refs": entries,
        # Every declared ref, so the cited terms and the prohibited ones.
        "evidence_refs": len(wide_context["terms"]) + len(wide_context["prohibited"]),
    }
    reasons = {entry["list"]: entry["reason"] for entry in catalog.page["truncated"]}
    for name, count in declared.items():
        assert f"{domain}.{name}" in reasons
        assert first["counts"][name] == count
    # One name no longer carries two states: a cut list and a withheld one say
    # which they are instead of leaving the caller to check for the key.
    assert reasons[f"{domain}.evidence_refs"] == WITHHELD
    assert reasons[f"{domain}.concepts"] == CUT
    # Recovery prose is present, and only that: after hy-74k and hy-6ae it is
    # advisory, everything a caller must act on is in `truncated` and `counts`,
    # and a test that greps it would pin wording this contract lets change.
    assert catalog.page["recovery"]


@pytest.mark.postgres
def test_the_inner_bound_is_not_the_callers_to_raise(session_factory, wide_context):
    """`limit` governs domains per page and nothing else. While one knob
    governed both axes the response was O(limit^2) (hy-ncp)."""
    at_the_cap = list_context_catalog(session_factory=session_factory, limit=CATALOG_MAX_LIMIT)

    (first, *_) = at_the_cap.domains
    assert len(first["concepts"]) == CATALOG_INNER_LIMIT
    assert first["counts"]["concepts"] == wide_context["entries"]
    assert {"list": f"{first['domain']}.concepts", "reason": CUT} in at_the_cap.page["truncated"]


@pytest.mark.postgres
def test_a_prohibition_is_never_cut_to_fit_a_bound(session_factory, wide_context):
    """`resolver.py`'s rule about `context_budget` -- a bound must not make a
    caveat or a prohibition disappear -- applies to every bound, not to one
    (hy-k8c). Proved on a corpus carrying more prohibitions than the inner
    bound, so a cut would show; prohibitions are small in real manifests, so
    exempting them costs nothing.

    A planner that cannot see a prohibition can approve the source it names.
    """
    entries = wide_context["entries"]
    catalog = list_context_catalog(session_factory=session_factory)
    first = catalog.domains[0]

    assert "prohibited_source_refs" in UNBOUNDED_LISTS
    assert entries > CATALOG_INNER_LIMIT
    assert len(first["prohibited_source_refs"]) == entries
    assert first["counts"]["prohibited_source_refs"] == entries
    assert not any(
        entry["list"].endswith(".prohibited_source_refs") for entry in catalog.page["truncated"]
    )


@pytest.mark.postgres
def test_a_second_document_kind_would_break_the_recovery_promise(session_factory, revenue_slice):
    """`documents` is capped like every other list, and `page.recovery` says
    resolving the domain returns it whole. That is true only because the
    schema permits exactly one document kind AND `git_instructions` serves
    that same one. Add a second and both halves break at once: the cap
    becomes trippable and resolve stops returning what was cut, so the
    recovery text starts lying.

    This fails the moment a kind is added without `git_instructions` learning
    to serve it -- deliberately, because the alternative is discovering it
    from a truncated catalog in production.

    Asserting the current set of kinds instead would be a change detector, not
    a coupling test: it would fail a *correct* addition too, and so would
    train the next person to update an expected value rather than to teach
    `git_instructions`. The subset check fails only when the two actually
    disagree.
    """
    snapshot = PostgresContextRepository(session_factory).get_snapshot(
        revenue_slice["context"].snapshot_id
    )
    kinds = set(snapshot.normalized["documents"])
    served = set(git_instructions(snapshot.normalized))

    assert kinds
    assert kinds <= served, (
        "a document kind the catalog can truncate but resolve does not serve: either "
        "teach git_instructions to serve it, or page.recovery is lying about this list"
    )


@pytest.mark.postgres
def test_next_offset_walks_more_than_one_page_of_domains(session_factory, wide_context):
    """Every earlier paging test ran at `domain_count == 1`, so continuation
    was never walked even once (hy-5b1)."""
    seen: list[str] = []
    offset = 0
    pages = 0
    while True:
        page = list_context_catalog(session_factory=session_factory, limit=2, offset=offset)
        seen.extend(entry["domain"] for entry in page.domains)
        pages += 1
        if page.page["next_offset"] is None:
            break
        offset = page.page["next_offset"]

    assert pages > 1
    assert seen == sorted(seen)
    assert len(seen) == wide_context["domains"]
    assert len(set(seen)) == len(seen)


@pytest.mark.postgres
def test_paging_past_the_last_domain_is_an_empty_page_not_a_repeat(session_factory, revenue_slice):
    first = list_context_catalog(session_factory=session_factory, limit=1)
    assert [entry["domain"] for entry in first.domains] == ["revenue"]
    assert first.page["next_offset"] is None

    beyond = list_context_catalog(session_factory=session_factory, limit=1, offset=1)

    assert beyond.domains == []
    assert beyond.page["domain_count"] == 1
    assert beyond.page["next_offset"] is None


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("kwargs", "key"),
    [
        ({"limit": CATALOG_MAX_LIMIT + 1}, "limit"),
        ({"limit": 0}, "limit"),
        ({"offset": -1}, "offset"),
    ],
)
def test_the_service_refuses_a_bound_it_will_not_serve(session_factory, revenue_slice, kwargs, key):
    """The service refuses rather than clamping, and it refuses here rather
    than only at the transports (hy-cy0). This is the API contract for
    in-process callers -- a script, another transport, a planner calling the
    service directly -- who must get the same answer an agent gets and not a
    silently different page."""
    with pytest.raises(CatalogBoundError) as excinfo:
        list_context_catalog(session_factory=session_factory, **kwargs)

    assert excinfo.value.key == key
    assert str(kwargs[key]) in str(excinfo.value)


@pytest.mark.postgres
def test_the_summary_matches_the_projection_it_replaces(session_factory, revenue_slice):
    """The catalog no longer materializes the graph to report its shape
    (hy-aq3). This is what stops the cheap derivation and the real
    projection from drifting apart."""
    snapshot = PostgresContextRepository(session_factory).get_snapshot(
        revenue_slice["context"].snapshot_id
    )
    instructions = git_instructions(snapshot.normalized)
    graph = domain_graph(snapshot, instructions, {"observed_assets": []})

    assert projection_summary(snapshot, instructions) == {
        "node_kinds": sorted({node["kind"] for node in graph["nodes"]}),
        "relationships": sorted({edge["relation"] for edge in graph["edges"]}),
    }


# --- hy-gh-287: an unevaluated domain is disclosed, never laundered as green ---


def _sync_unevaluated_domain(session_factory, root, *, domain="unmeasured"):
    """Sync a fully-resolvable domain that declares `evals: none` (hy-gh-287).

    The revenue fixture with a real eval bank, minus the bank: same definitions,
    sources and fields -- so it resolves to a governed bundle -- but `evals:
    none` and no `evals.yaml`, which is exactly the domain the feature exists to
    let onboard without fabricating coverage."""
    import shutil

    import yaml

    from hyperset.context.sync import sync_git_context
    from tests.integration.test_git_context_source import CONTEXT_PATH, FIXTURE_DIR, git

    repository = root / f"{domain}-repo"
    (repository / CONTEXT_PATH).mkdir(parents=True)
    git("init", "--quiet", "--initial-branch=main", ".", cwd=repository)
    git("config", "user.email", "context@example.test", cwd=repository)
    git("config", "user.name", "Context Owner", cwd=repository)
    for path in sorted(FIXTURE_DIR.iterdir()):
        if path.name == "evals.yaml":
            continue
        shutil.copy(path, repository / CONTEXT_PATH / path.name)
    manifest = yaml.safe_load((FIXTURE_DIR / "manifest.yaml").read_text())
    manifest["domain"] = domain
    manifest["evals"] = "none"
    (repository / CONTEXT_PATH / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    git("add", "-A", cwd=repository)
    git("commit", "--quiet", "-m", "a domain that declares no eval bank", cwd=repository)

    source = PostgresContextRepository(session_factory).register_source(
        repository=str(repository), ref="main", path=CONTEXT_PATH
    )
    result = sync_git_context(
        source_id=source.id, session_factory=session_factory, cache_dir=root / "cache"
    )
    assert result.status == "synced", result.reasons
    return domain


@pytest.mark.postgres
def test_the_catalog_discloses_which_domains_are_unevaluated(
    session_factory, revenue_slice, tmp_path
):
    """`evals: none` onboards and is a visible fact, not an absence (hy-gh-287):
    the count is stated on every domain and the corpus aggregate is kept apart
    from the domain total, so a reader can never mistake an unmeasured domain for
    a measured one."""
    _sync_unevaluated_domain(session_factory, tmp_path)

    catalog = list_context_catalog(session_factory=session_factory)
    by_domain = {entry["domain"]: entry for entry in catalog.domains}

    # A real bank counts; a declared-none domain shows zero, stated not omitted.
    assert by_domain["revenue"]["counts"]["eval_cases"] >= 1
    assert by_domain["unmeasured"]["counts"]["eval_cases"] == 0
    # The honest aggregate, over all domains, separate from `domain_count`.
    assert catalog.page["domain_count"] == 2
    assert catalog.page["unevaluated_domain_count"] == 1


@pytest.mark.postgres
def test_a_resolved_bundle_states_only_an_unevaluated_domains_lack_of_coverage(
    session_factory, revenue_slice, tmp_path
):
    """The bundle discloses `unevaluated` on the domain that declared no bank and
    NOTHING on the domain that has one (hy-gh-287): a domain with a real bank
    answers byte-for-byte the authority it did before the field existed, so an
    unmeasured domain is never mistaken for a passing one, and an evaluated one
    never carries a redundant `unevaluated: false`."""
    _sync_unevaluated_domain(session_factory, tmp_path)
    catalog = {
        entry["domain"]: entry
        for entry in list_context_catalog(session_factory=session_factory).domains
    }

    unmeasured = resolve_analytics_context(
        query="What guidance exists for the unmeasured domain?",
        directive=ContextDirective(
            domains=["unmeasured"], concepts=catalog["unmeasured"]["concepts"]
        ),
        session_factory=session_factory,
    )
    revenue = resolve_analytics_context(
        query="Which source and rules should an analyst use for recognized revenue by region?",
        directive=ContextDirective(
            domains=["revenue"], concepts=catalog["revenue"]["concepts"], asset_refs=[APPROVED_REF]
        ),
        session_factory=session_factory,
    )

    assert unmeasured.context_authority["unevaluated"] is True
    assert "unevaluated" not in revenue.context_authority
