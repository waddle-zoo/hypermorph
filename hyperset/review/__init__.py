"""Review-surface helpers that are PURE (no subprocess, no SQL, no network).

Kept a separate, minimal package so a caller pinned pure -- notably
`hyperset.transport.operations` (tests/unit/evals/test_report_time_purity.py) --
can import a helper here without pulling in `hyperset.context`'s git reader or
`hyperset.flywheel.git_pr`'s subprocess writer through a package __init__.
"""
