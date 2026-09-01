# Observed reference evidence

Real charts and a real dashboard from the repository's pinned Apache Superset
6.1.0 Docker environment, captured so that the two references a dataset's use is
countable from are evidence rather than a test construction (hy-vzk8):

- chart `--queries-->` dataset
- dashboard `--contains-->` chart

Contents:

- `manifest.json` -- the machine-readable identity and reference contract, plus
  a sha256 per captured record;
- `official-export.zip` -- the unmodified dashboard export the instance
  produced. One archive holds the dashboard, its three charts, the two datasets
  they query, and the database. This is the transport the connector reads;
- `rest/` -- the unmodified chart and dashboard REST bodies. Captured to pin
  what live REST discloses about these types: identity (`uuid`) and both
  references (`datasource_uuid`, `position_json`) are all served, under
  different field names than the export uses. The connector reads both
  spellings (hy-rt4v), so these bodies are what live REST normalization is
  tested against -- a build that stopped serving either field reds here;
- `secret-scan.json` -- the credential labels checked and any findings.

Regenerate with `make up-demo && make demo-bootstrap-usage &&
make demo-generate-usage-evidence`. The seed is idempotent and creates no
dataset, so it never rewrites what `revenue/` captured.

`customer_dim` is deliberately left with zero charts: a reference count that
distinguishes datasets has to include one that nothing references. It is absent
from the export entirely, which is why `manifest.json` names it under
`uncovered_by_this_capture` instead of implying every seeded dataset appears.
