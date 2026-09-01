# 0042: The first public release is v0.0.1

Status: Accepted (release ruling, 2026-08-30).

This record EXTENDS [ADR 0015](0015-no-release-process-until-a-publication-event.md)
and SUPERSEDES only the release-name and time-box language in
[ADR 0039](0039-v0.1.0-enterprise-readiness-release-focus.md). ADR 0039 remains the
historical record of that enterprise-hardening cycle; its scope reasoning is not rewritten.

## Context

ADR 0015 measured that Hyperset had never published a tag, PyPI artifact, or container
image. It also established that `version = "0.1.0"` in `pyproject.toml` was a placeholder,
not evidence of a release. ADR 0039 later called its hardening cycle “v0.1.0,” but that
cycle name did not create a publication event. The first public release is now being
prepared under the v0.0.1 name.

Without a successor record, the repository simultaneously tells readers that v0.1.0 is
the current release target and that the first public artifact will be v0.0.1. Editing ADR
0039 would erase the original decision instead of explaining the change.

## Decision

1. The first public Hyperset release target is **v0.0.1**.
2. No v0.1.0 release is claimed: no tag or public artifact existed under that name.
3. ADR 0039 remains historical. This record supersedes only its release label and “until
   v0.1.0 ships” time box, not its enterprise-hardening rationale or the authority ADRs it
   cites.
4. ADR 0015 still governs publication mechanics. The release event sets the package
   version and assembles the registered release notes; the existing `0.1.0` package value
   remains a placeholder until that release change is made.

## Release note

The v0.0.1 product is the Hyperset Hive-Mind: a flexible-yet-governed analytics knowledge
graph served through Live chat, Explore the Hive-Mind, Review, and Settings. The demo and
served model path use OpenAI/Luna; Ollama/Qwen remain isolated benchmark fixtures. Agents
may search, reason, and propose, but observed evidence and proposals never become canonical
until a human-owned Git review merges them.

## Consequences

- Documentation and release gates refer to v0.0.1 as the first public release.
- Historical references to the “v0.1.0 enterprise-readiness cycle” remain readable as
  history and point here for current release identity.
- This decision changes no product API, schema, authority boundary, or runtime behavior.
- A later publication follows ADR 0015 rather than inferring a release from a placeholder
  in `pyproject.toml`.
