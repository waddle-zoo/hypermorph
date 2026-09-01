# Canonical revenue evidence

This directory contains authoritative evidence generated from the repository's
pinned real Apache Superset 6.1.0 Docker environment:

- `manifest.json` is the shared machine-readable scenario and identity contract;
- `proposal.yaml` is one unapproved human-review input, not runtime authority;
- `baseline/`, `drift/`, and `restored/` retain unmodified REST response bodies
  and official export ZIPs;
- `secret-scan.json` records the credential labels checked and any findings.

Regenerate with `make up-demo && make demo-generate-evidence`. The controlled
drift changes only `recognized_revenue`'s Superset metric expression from
`SUM(gross_amount - tax_amount)` to `SUM(gross_amount)`, captures the source,
then restores and verifies the baseline controlled-property hash.

Fixtures elsewhere under `tests/fixtures/superset/` are hand-written,
supplemental parser coverage. They are not evidence of upstream compatibility.
