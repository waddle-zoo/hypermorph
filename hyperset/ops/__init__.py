"""The read-only operator view (hy-gh-72, design docs/development/ops-read-view.md).

A presentation layer over what already exists: it SURFACES sync health, the
pinned Git context, linked evidence, findings, and eval state so an operator can
see whether Hyperset is telling the truth without a psql prompt. It never asserts
truth and never mutates -- every function here reads through repository readers
only. The read-only boundary is enforced as code by
`tests/unit/ops/test_read_only_guard.py`, which reds if anything in this package
reaches a repository WRITER.

Slice 1 (hy-9vji) is sync health; S2-S5 add the other surfaces to this package.
"""
