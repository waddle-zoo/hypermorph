# 0005: Human approval is mandatory for governed meaning

Status: accepted.

## Context

Agents can discover patterns and propose context faster than humans can
review them one at a time, which creates pressure toward auto-approval as
a scaling shortcut. `MANIFESTO.md`'s core thesis is the opposite: "AI
should propose context. Humans should own canonical meaning."

## Decision

No code path advances a `GovernedContext` to a new version without a
`ReviewDecision` (`hyperset.repositories.ReviewRepository.approve`,
`decided_by` required, non-null). Approval is transactional: the new
context version and the decision record are created together or not at
all. Review tasks are deduplicated by idempotency key so a processor can
re-run safely without spamming duplicate approval requests. Optimistic
concurrency (`ReviewTask.row_version`) prevents two reviewers from
silently overwriting each other's decision on the same task.

Bulk operations (approving many low-risk, no-conflict candidates at once)
remain a human action, not an automatic one — the lever for scale is
making review fast and well-explained, not skipping it.

## Consequences

- Every approved fact has exactly one accountable human decision behind
  it, always inspectable.
- Throughput is bounded by review capacity, not connector/processor
  throughput — accepted as the point, not a limitation to route around.
- `#38`/`#39` (processor, review UI) own making that review experience
  fast; this ADR constrains what they're allowed to skip, not how they
  present the queue.
