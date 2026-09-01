# Ownership-mismatch activation -- design (hy-z3wy, follow-on to hy-ocbd)

Status: DESIGN, BUILD-GATED (2026-08-17, hy-z3wy). This is an ACTIVATION FORK: it
requires an explicit overseer/Brandon ruling before any build, and nothing is built
until that ruling lands (see section 3). Scope: WIRE the already-shipped
`ownership_mismatch` producer so a bundle emits it when the customer DECLARES an
identity bridge in Git. The `identity` comparator
(`hyperset/bundle/reconcile.py` `COMPARATORS[IDENTITY]`), the `ownership_mismatch`
producer, and its `RECONCILED_KINDS` entry all shipped inert at SV20 (hy-ocbd). The
new things here are a GOVERNED MANIFEST FIELD and the resolver call that reads it --
and both are why this is a fork, not a mechanical wiring.

## 1. The governed manifest field (identity bridge)

An approved-source owner in Git (`team:finance-data`) and a catalog owner
(`urn:li:corpuser:jdoe`) live in DIFFERENT identifier spaces, so the two are never
bridged by heuristic (a wrong bridge costs a FALSE error finding). The bridge is
GOVERNED content the customer DECLARES, or nothing is reconciled.

Proposed manifest shape (single declared identity per owner this cut; multi-owner
/ set semantics is the documented refinement hy-imr0):

```yaml
owner_bridges:
  - ref: team:finance-data          # a governed owner_ref (Git identifier space)
    identity: urn:li:corpuser:jdoe   # the catalog identity it corresponds to
```

Parsed into `snapshot.normalized["owner_bridges"]` as declared, like
`prohibited_sources` and the per-source `facets`. It would be added to
`SUPPORTED_MANIFEST_FIELDS` (or an undeclaring manifest that used the name would be
REJECTED at parse) and to `to_manifest_document` (the hy-gh-43 round-trip), and
documented in v0-foundation section 8's manifest register. It is intended to be
parse-not-surface -- `hyperset/bundle/instructions.py::git_instructions` enumerates
a FIXED key set and would not copy it, and the Git `normalized` dict is served
nowhere raw -- so it would not reach the served bundle or `bundle_id`.

## 2. Resolver wiring (reuse the slice-(ii) dispatch, no per-dimension branch)

In `_linked_evidence`, beside the existing `source_deleted_while_governed` and
`prohibited_but_referenced` calls, a guarded
`conflicts.extend(ownership_mismatch(bridge, observed_owner=..., commit_sha=...))`,
called ONLY when a bridge is declared and routed through the one `reconcile()`
mechanism -- no ownership-specific comparator or branch.

The observed owner for each bridged `owner_ref` would be read from the domain's
approved-source (GIT_LINKED) observed assets -- the DataHub connector already stores
`owner_urns` on the observed asset. Proposed v0 cut (approved-choice pending the
ruling): the SINGLE distinct observed owner urn across those assets; where the
estate reports none, or more than one, stay SILENT (an absence / an ambiguity, never
guessed or aggregated). Superset carries no owner in this sense, so ownership
mismatch would be a DataHub-estate capability, silent elsewhere.

## 3. Classification -- ACTIVATION FORK (build-gated)

This activation is a FORK and requires an explicit overseer/Brandon ruling before
any build. That is the authoritative classification, and it is NOT overridden by a
byte-level additivity argument:

- **`reconcile.py`'s shipped contract governs.** The producer's own docstring states
  the wiring is "contract-adjacent" and "forks first because a new governed manifest
  field is contract-adjacent." That shipped contract is authoritative; we do not
  ratify around it.
- **A declaring user gains a NEW GOVERNED INPUT.** `owner_bridges` is a new governed
  manifest field -- a new thing the customer's Git commit can say and that Hyperset
  parses, stores, and acts on. Adding to the governed input surface is a contract
  decision regardless of whether the OUTPUT bytes move for non-declaring users.
- **A declaring user gains newly-ACTIVATED served conflicts.** Once wired, a declared
  bridge that disagrees produces `ownership_mismatch` entries in
  `linked_evidence.conflicts` that no prior version ever emitted -- a real change to
  what that user is served, and to what an assist reasoner may cite.

SUPPORTING EVIDENCE (does not downgrade the fork): the change is designed to be
byte-additive for a NON-declaring user and to move neither `SCHEMA_VERSION` nor
`tools_hash`:

- a non-declaring user has no `owner_bridges`, so the producer is never called and
  the field is parse-not-surface -- the served bundle, including `bundle_id`, is
  intended byte-identical;
- new `conflicts[]` entries carry an already-registered `RECONCILED_KINDS` kind and
  keys shipped at SV20, so no new served KEY appears -- no `SCHEMA_VERSION` move;
- `conflicts` is bundle output, not a served tool name/description/input schema -- no
  `tools_hash` change.

This evidence bounds the blast radius; it does not make the activation not-a-fork.
The overseer has logged this for Brandon. **BUILD NOTHING until it is ruled.**

## 4. The build slice (ONLY after the ruling)

1. Parse `owner_bridges` into `normalized` (manifest parser), optional, absent-safe;
   add it to `SUPPORTED_MANIFEST_FIELDS`, `to_manifest_document`, and section 8.
2. Add `_observed_owners_for` and the guarded `conflicts.extend(ownership_mismatch(...))`
   in `_linked_evidence`.
3. Regression (mutation-verified): a DECLARED bridge that disagrees emits exactly one
   `ownership_mismatch`; agreement is silent; a non-declaring bundle is BYTE-IDENTICAL
   (assert `bundle_id` unchanged); an ambiguous (none/many) observed owner emits
   nothing; the two identifier spaces are never heuristically bridged.
4. A postgres resolve-path test asserting the conflict reaches the served bundle, and
   the non-declaring byte-identical assertion; confirm `SCHEMA_VERSION` stays 20 and
   `tools_hash` stays `sha256:fe930a003b731211`.

## See also

- `docs/development/reconciliation-unlock-triggers.md` section 2 (why it was refused,
  the declaration that unlocks it).
- `hyperset/bundle/reconcile.py` (`ownership_mismatch`, `IDENTITY`, `RECONCILED_KINDS`
  -- and the "contract-adjacent, forks first" contract this fork honors).
- hy-imr0 (multi-owner / set semantics refinement), hy-yjkv (grain activation),
  hy-kh9k (freshness activation) -- sibling activations that face the same fork.
