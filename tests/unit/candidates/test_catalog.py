"""Catalog candidate discovery: ranking, disclosure, reachability, floors (hy-gh-206).

Pure ranking over the deterministic double. The double ranks by surface-token
overlap, which is enough to prove position-independence, disclosure, and the
governance shape; it is not the semantic paraphrase proof, which is the real
model's job in the benchmark.
"""

from __future__ import annotations

from hyperset.candidates.catalog import (
    ASSIST_RANKING,
    CONCEPT,
    DOMAIN,
    Candidate,
    DomainFacts,
    discover_candidates,
)
from hyperset.embedding.deterministic import DeterministicEmbeddingProvider

# A revenue domain whose query-matching term sits at index 12, past the
# INNER_LIMIT=10 cap the catalog preview would apply, among unrelated fillers.
_FILLER = (
    "penguin",
    "glacier",
    "tundra",
    "aurora",
    "basalt",
    "lichen",
    "moss",
    "fjord",
    "quartz",
    "cobalt",
    "willow",
    "cedar",
)
REVENUE = DomainFacts(
    domain="revenue",
    title="Revenue",
    concepts=(*_FILLER, "expansion revenue", "seat count"),
    commit_sha="c0ffee",
    context_snapshot_id="cs-rev",
)
HABITAT = DomainFacts(
    domain="habitat",
    title="Penguin Habitat",
    concepts=("ice shelf", "krill density"),
    commit_sha="deadbee",
    context_snapshot_id="cs-hab",
)
CORPUS = (REVENUE, HABITAT)


def _provider():
    return DeterministicEmbeddingProvider(dimensions=256)


def test_deterministic_same_inputs_same_ordered_list():
    a = discover_candidates(question="expansion revenue", corpus=CORPUS, provider=_provider())
    b = discover_candidates(question="expansion revenue", corpus=CORPUS, provider=_provider())
    assert [c.to_dict() for c in a] == [c.to_dict() for c in b]


def test_tie_break_orders_a_fully_tied_group_by_kind_then_domain_then_term():
    # An empty query is the zero vector, so cosine is exactly 0.0 for every
    # candidate -- a TOTAL tie, no hashing collision can perturb it -- and the
    # whole order is the tie-break's doing. Insertion order leads with the
    # first domain (revenue); the tie-break leads with a concept (kind
    # "concept" < "domain"), the lowest domain ("habitat"), lowest term
    # ("ice shelf"). A score-only sort key keeps insertion order for the tie
    # and puts the revenue DOMAIN first, so this fails against it -- which is
    # the point: reproducibility alone (the previous test) is blind to the key.
    out = discover_candidates(question="", corpus=CORPUS, provider=_provider())
    assert all(c.signal["score"] == 0.0 for c in out)
    assert (out[0].kind, out[0].domain, out[0].term) == (CONCEPT, "habitat", "ice shelf")
    # Domains sort after every concept in a tie; the last two are the domains.
    assert out[-1].kind == DOMAIN and out[-2].kind == DOMAIN


def test_a_relevant_concept_past_the_positional_cap_is_reachable():
    # "expansion revenue" is concept index 12; a positional preview [:10] drops
    # it. Discovery ranks by relevance, so it comes back near the top.
    assert "expansion revenue" not in REVENUE.concepts[:10]
    out = discover_candidates(question="expansion revenue", corpus=CORPUS, provider=_provider())
    concept_terms = [c.term for c in out if c.kind == CONCEPT and c.domain == "revenue"]
    assert "expansion revenue" in concept_terms
    top_revenue_concept = next(c for c in out if c.kind == CONCEPT and c.domain == "revenue")
    assert top_revenue_concept.term == "expansion revenue"


def test_relevance_reaches_the_right_domain():
    out = discover_candidates(question="expansion revenue", corpus=CORPUS, provider=_provider())
    domains = [c for c in out if c.kind == DOMAIN]
    revenue_rank = next(i for i, c in enumerate(domains) if c.domain == "revenue")
    habitat_rank = next(i for i, c in enumerate(domains) if c.domain == "habitat")
    assert revenue_rank < habitat_rank


def test_every_candidate_discloses_the_signal_that_ranked_it():
    out = discover_candidates(question="expansion revenue", corpus=CORPUS, provider=_provider())
    assert out
    for candidate in out:
        signal = candidate.signal
        assert isinstance(signal["score"], float)
        assert signal["matched_on"] in {DOMAIN, CONCEPT}
        # The index the ranking is reproducible against: the space, the
        # snapshot, and the source-text hash (ADR 0022).
        for key in (
            "provider",
            "model",
            "dimensions",
            "input_projection_version",
            "source_text_hash",
            "commit_sha",
            "context_snapshot_id",
            "index_version",
            "space_id",
        ):
            assert key in signal


def test_a_candidate_carries_no_governed_label_and_no_asset_ref():
    out = discover_candidates(question="expansion revenue", corpus=CORPUS, provider=_provider())
    for candidate in out:
        assert candidate.provenance == ASSIST_RANKING
        # The shape holds no observed-asset ref, no resolution, no status: the
        # keys are a fixed allowlist, so a ranking cannot become a link or a
        # verdict by carrying one.
        assert set(candidate.to_dict()) == {"kind", "domain", "term", "provenance", "signal"}
    blob = repr([c.to_dict() for c in out])
    for forbidden in ("governed", "approved", "canonical", "trusted", "asset_refs", "resolution"):
        assert forbidden not in blob


def test_the_candidate_type_has_no_field_that_could_hold_a_declared_ref():
    # ADR 0019: assist output has no slot for the declared ref that would make
    # a ranking into a link. Enforced by shape, asserted here so adding one is
    # a red test rather than a review miss.
    assert set(Candidate.__dataclass_fields__) == {"kind", "domain", "term", "signal", "provenance"}


def test_limit_bounds_the_list():
    out = discover_candidates(question="revenue", corpus=CORPUS, provider=_provider(), limit=3)
    assert len(out) == 3


def test_an_empty_corpus_returns_no_candidates_without_embedding():
    # Nothing to rank must not reach the provider, so a deployment with no
    # configured context serves discover with no embedding backend at all.
    class _Exploding:
        space = DeterministicEmbeddingProvider(dimensions=8).space

        def embed_query(self, text):
            raise AssertionError("empty corpus must not embed the question")

        def embed_documents(self, texts):
            raise AssertionError("empty corpus must not embed documents")

    assert discover_candidates(question="anything", corpus=(), provider=_Exploding()) == []


def test_discovery_redacts_credentials_before_hosted_provider_input():
    class _RecordingProvider:
        def __init__(self):
            self.delegate = _provider()
            self.query = None

        @property
        def space(self):
            return self.delegate.space

        def embed_query(self, text):
            self.query = text
            return self.delegate.embed_query(text)

        def embed_documents(self, texts):
            return self.delegate.embed_documents(texts)

    provider = _RecordingProvider()
    discover_candidates(
        question="revenue https://alice:ghp_QUERYSECRET@example.com/definitions",
        corpus=CORPUS,
        provider=provider,
    )
    assert provider.query == "revenue https://example.com/definitions"


def test_a_domain_candidate_names_its_domain_and_carries_no_term():
    out = discover_candidates(question="revenue", corpus=CORPUS, provider=_provider())
    for candidate in out:
        if candidate.kind == DOMAIN:
            assert candidate.term is None
            assert candidate.domain in {"revenue", "habitat"}
