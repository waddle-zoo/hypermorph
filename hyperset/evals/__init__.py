"""The benchmark (GitHub #25): two local arms, one substrate difference.

Split by what a machine needs to run it. Scoring the committed recordings
needs this repository and nothing else -- no model, no hosted credential, no
GPU -- which is what makes ADR 0013's required per-PR gate affordable.
Recording a live arm needs a real Ollama and a real database, and runs on a
schedule.

Nothing here is a served surface. The raw baseline's tools reach the
observation store read-only inside the benchmark and are never mounted on HTTP
or MCP: the governed benchmark stays on its explicit three-operation
`RESOLVE_PATH_OPERATIONS` allowlist. Additional served assist/audit operations
need evaluator evidence and an ADR amendment; they do not enter that allowlist.
"""
