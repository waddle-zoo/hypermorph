# ADR 0023: Govern table and pipeline identities; make BI objects explicit overrides

Status: Accepted

## Context

The v0 manifest made every governed source use an observed connector identity,
such as a Superset dataset UUID or DataHub URN. That reverses the authority
boundary established by ADRs 0012 and 0017: transient catalog and BI objects
become the identity of company meaning, even though they are evidence about
that meaning and may change through their own UIs.

The durable object a data team can review beside its code is normally a table
or pipeline. Some organizations do intentionally govern a published BI
semantic object, so forbidding that relationship entirely would also lose a
real policy choice.

## Decision

1. `approved_sources[].ref`, `prohibited_sources[].ref`, `fields[].source_ref`,
   and locked evaluation source refs identify a table or pipeline using
   `<table|pipeline>:<platform>:<external_id>`.
2. Definitions contain company meaning only. They do not embed DataHub or
   Superset evidence refs.
3. A source may opt into one exact `bi_override` with a required `ref` and
   `reason`. V0 accepts only a `superset:dataset:<external_id>` override.
4. The override is an additional governed way to address that source. It is
   corroborated through the existing observation path and never replaces the
   durable table or pipeline identity.
5. Superset and DataHub assets without an explicit override remain observed
   evidence. Hyperset does not guess their relationship from names.
6. This is a pre-release replacement of the manifest contract. No migration,
   compatibility parser, new store, or new HTTP/MCP operation is added.

## Consequences

- Git context remains stable when a BI object is recreated or catalog metadata
  changes.
- Humans review source meaning at the table/pipeline layer while connectors
  continue to provide drift, lineage, and freshness evidence.
- Organizations that deliberately govern a Superset semantic dataset must say
  so explicitly and explain why.
- An override that is missing, ambiguous, stale, or deleted is disclosed by the
  existing evidence machinery; it cannot erase the Git-owned source.
- Automatic table-to-catalog or table-to-BI matching remains future work and
  would require evidence strong enough to avoid creating false authority.
