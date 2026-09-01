"""The customer-facing eval runner (eval migration 1/3, hy-myn6).

A customer points at THEIR own cases and runs an Inspect AI eval of a governed
Hyperset deployment, scored by the ONE deterministic domain scorer this project
owns. Inspect AI owns the run loop, the dataset, the task and the reporting; this
package owns only what a governed-context predicate MEANS -- the irreducible body
that stock Inspect scorers cannot express (governed-vs-assist correctness,
source identity, no_match).

This slice is ADDITIVE: it ships alongside the existing GitHub #25 benchmark
harness (`hyperset.evals`), which is untouched. `inspect_ai` is an OPTIONAL extra
(`pip install 'hyperset[evals]'`); nothing in core Hyperset imports this package,
so a core install acquires no eval dependency. The relocation/consolidation of the
#25 harness is the destructive follow-on (hy-jeep / hy-bbtx), not this bead.
"""

from hyperset.eval.runner import DEFAULT_TESTING_MODEL, customer_eval_task
from hyperset.eval.scorer import governed_context_predicates

__all__ = [
    "DEFAULT_TESTING_MODEL",
    "customer_eval_task",
    "governed_context_predicates",
]
