"""What a planner asks Hyperset to retrieve (hy-x7f), GitHub #70.

The directive is the structured output of the caller's own planning agent,
and it replaces query interpretation. Hyperset used to pick a domain by
splitting the question into words and checking whether a configured domain
name was among them; that was handcrafted natural-language understanding and
the seed of an alias/keyword/scoring subsystem #70 forbids. Semantic work
belongs to the model. Naming exact things belongs to the caller. Resolving
exactly what was named belongs here.

So retrieval resolves; it does not decide. Nothing in this package ranks,
scores, expands a term into likely synonyms, or matches by similarity. A
directive that names nothing is refused with instructions to look at the
catalog first, because guessing would be the deleted behaviour under a new
name.

The shape is deliberately smaller than #70's illustrative one: `concepts`,
`document_hints`, and `relationships` have no consumer in this slice, and
consuming a concept or document hint would mean matching titles -- the very
thing being deleted. They ship when something needs them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Named in every refusal, so a caller that planned nothing learns where to
# start rather than being told only that its request was empty.
CATALOG_OPERATION = "list_context_catalog"

PLAN_FIRST = (
    f"call {CATALOG_OPERATION} to see the domains, concepts, documents, and source refs "
    f"that exist, have your planner choose from them, and resolve again with a "
    f"'directive' naming 'domains' and/or 'asset_refs'. Hyperset does not infer a "
    f"domain from the wording of the question"
)


@dataclass(frozen=True)
class ContextDirective:
    """Exact things to retrieve, bounded.

    `domains` names configured Git context to resolve; `asset_refs` names
    exact observed evidence, and any ref the resolved domain does not cover
    comes back as observed-only rather than being dropped or promoted.
    """

    domains: list[str] = field(default_factory=list)
    asset_refs: list[str] = field(default_factory=list)
    # The coverage claim (hy-9lct): the concept terms this answer needs the
    # named domain to declare, spelled exactly as the catalog lists them.
    # Required with `domains`, because a domain named with no claim is how a
    # supplier-lead-time question got a governed revenue bundle, and refused
    # without one, because it names what nothing would verify it against.
    concepts: list[str] = field(default_factory=list)
    # Bounds the domain-graph projection by distance from the domain node.
    # `None` is the whole projection. It never trims `instructions`: a
    # smaller packet may not mean quieter governed meaning, or a caveat
    # would disappear because a budget was tight.
    max_hops: int | None = None
    # Bytes of the serialized bundle. Honoured deterministically and with
    # disclosure -- see `_within_budget` in the resolver for what gives way.
    context_budget: int | None = None
    # ADR 0019 decision 1's refusal half (hy-c9mb): "assist can be refused,
    # never demanded". `False` is the only value that changes anything -- it
    # drops the `assist` section from a coverage refusal, leaving the governed
    # answer alone, including its silence. `True` is the default and passing it
    # is a NO-OP by construction: assist runs where governance is silent whether
    # or not a caller asked, so there is no state in which setting this makes a
    # section appear that absence would not have produced. That asymmetry is the
    # decision, not a convention -- an evaluation arm, a conformance run or a
    # caller under a compliance obligation has a reason to see governance alone,
    # and the opposite direction has no legitimate use.
    assist: bool = True

    def __post_init__(self) -> None:
        """`domains` and `concepts` are one parameter in two halves, and a
        directive carrying one without the other is malformed (hy-bdff).

        Both halves are refused here, for one reason. A coverage claim with
        no domain to check it against would be a parameter the contract
        accepts, echoes back in `request.directive`, and never acts on --
        which reads to a caller as a claim that was honoured. A domain with
        no claim is the same defect pointing the other way: a required
        parameter is absent, which is knowable before any retrieval runs.
        Answering that with a bundle told the caller "no configured Git
        context covers this request" about a request a configured Git context
        does cover. A refusal says what is wrong with the request; a bundle
        says what is true of the corpus, and a missing parameter is not that.
        """
        # Casefold `domains` to the SAME canonical form the stored snapshot uses
        # (hy-gh-282): `parse_context` casefolds the manifest domain, so the
        # request side must too, or a request naming 'Revenue' would miss a
        # 'revenue' store (exact `in` match) and a previously-resolvable domain
        # would break under this change. With this, the store, the collision
        # query, and this request genuinely agree on case. A frozen dataclass, so
        # the normalization goes through `object.__setattr__`. (A pre-existing
        # mixed-case STORED domain re-canonicalizes on its next sync; none ship,
        # and no migration is added -- the detection-not-constraint design stands.)
        object.__setattr__(self, "domains", [domain.strip().casefold() for domain in self.domains])
        if self.concepts and not self.domains:
            raise ValueError(
                "'concepts' names what a domain must declare, and this directive names no "
                "'domains': name the domain the concepts belong to, or drop them"
            )
        if self.domains and not self.concepts:
            raise ValueError(
                "'domains' names context to resolve without saying what it must cover, and no "
                f"governed context is served on an unstated claim: call {CATALOG_OPERATION} and "
                "name in 'concepts' the exact terms this answer needs, as it lists them for that "
                "domain"
            )

    @property
    def is_empty(self) -> bool:
        return not self.domains and not self.asset_refs

    def to_dict(self) -> dict:
        payload = {
            "domains": list(self.domains),
            "asset_refs": list(self.asset_refs),
            "concepts": list(self.concepts),
            "max_hops": self.max_hops,
            "context_budget": self.context_budget,
        }
        # Echoed only when it is FALSE (hy-c3dl). A declining caller still gets
        # its refusal confirmed rather than inferring it from an absent section,
        # which an absent `assist` and a declined one are otherwise
        # indistinguishable by. But `assist: true` on every other answer would
        # advertise a field the transports refuse on input -- the allow-list and
        # the MCP schema still take five keys until hy-hj9g -- so a caller
        # reading the echo would be told to send something that is rejected.
        # Echoing the default buys nothing a reader cannot get from the absence.
        if not self.assist:
            payload["assist"] = self.assist
        return payload
