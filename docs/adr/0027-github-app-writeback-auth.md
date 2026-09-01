# 0027: Write-back authenticates as a GitHub App via short-lived installation tokens

> **Amended (scope note) by [ADR-0036](0036-bring-your-own-knowledge-graph-authority-adapters.md) (PROPOSED).** GitHub-App installation-token auth is the Git INSTANCE of a general "authority-backend write credential": secret-by-reference, encrypted-at-rest, fail-closed, minted-not-stored. A non-Git (KG) target brings its own credential under the same floor.

Status: ACCEPTED — the Overseer directed this P1 (2026-08-09) and approved the
design in the hy-bdhg dispatch; ships the normal loop (hyperion implements, critic
security-reviews at exact head, oversight merges). This ADR records the design as
built.

Extends ADR 0012 (authority is a human Git merge) and the write-back token model
of hy-eji4 (env-ref) and ADR 0026 (encrypted-at-rest PAT). It changes no approval
boundary and adds no served operation: this is admin CONFIG for the proposal-only
writer, not an MCP tool, so `tools_hash` is unaffected by it
(`sha256:fe930a003b731211` since hy-gh-281 item 3; this config does not move it).

## Context

Both existing URL-target auth paths present a stored, long-lived GitHub personal
access token: `env_ref` reads it from the environment by name (hy-eji4), and
`encrypted` stores it encrypted at rest (ADR 0026). A PAT is broad, long-lived,
and tied to a human user. The enterprise-appropriate credential for a machine
opening PRs into a customer repository is a GitHub App: it is installed on
specific repositories with explicit permissions, its writes appear as
`hyperset[bot]`, and it authenticates through SHORT-LIVED installation tokens
minted on demand rather than a stored standing secret. The Overseer directed a
GitHub App path as the enterprise DEFAULT, keeping `env_ref` and `encrypted` for
simple and legacy setups.

## Decisions

1. **A third token source, `WritebackConfig.token_source = 'github_app'`, the
   enterprise default.** `env_ref` and `encrypted` are BYTE-UNCHANGED; the config
   row's `token_source` selects the path. `env_ref`/`encrypted` remain for simple
   and legacy deployments.

2. **The App private key is the only stored secret, encrypted at rest via the
   EXISTING `hyperset.security.secret_box`.** New nullable columns on
   `context_writeback_config` hold `app_id` (an integer, not a secret) and the
   App private key as `app_key_ciphertext`/`app_key_nonce` (AES-256-GCM, the same
   KEK-from-`HYPERSET_SECRET_KEY` model as ADR 0026 — no second crypto path). The
   private key is WRITE-ONLY in the admin UI: pasting the `.pem` stores its
   ciphertext; the status/get response returns `{token_source, token_set, app_id}`
   and never the key. The key is never logged, never in an HTTP response body,
   never in the JS bundle, and never in an error string.

3. **Installation tokens are minted per operation and never stored.** At propose
   time, for a `github_app` URL target, the server:
   - decrypts the App private key in memory with the KEK;
   - signs a short-lived App JWT (RS256, PyJWT): `iss=app_id`, `iat=now-60`,
     `exp=now+540` (under GitHub's 10-minute cap);
   - resolves the App installation on the target repository
     (`GET /repos/{owner}/{repo}/installation`);
   - exchanges the JWT for an installation token scoped to that repository with
     `contents:write` + `pull_requests:write`
     (`POST /app/installations/{id}/access_tokens`).
   The minted token (~1 hour) is handed only to the git push child's environment
   via the `GIT_CONFIG_*` mechanism of hy-6haz — never a command-line argument,
   never a remote URL — and discarded after the push. It is never persisted; there
   is no column for it.

4. **Fail closed at every step.** A missing App id or private key, an unset or
   mismatched KEK, an undecryptable key, an App not installed on the repository,
   or any JWT/mint failure REFUSES the propose with a clear error. There is no
   fallback to an unauthenticated push. Error strings name only the repository and
   the GitHub HTTP status — never the private key or the minted token.

5. **Admin surface only, same gate as the rest of the write-back config.** The
   write API accepts the App id and the raw `.pem` over the ADMIN surface only
   (admin prefix, off the public surface), exactly like the `env_ref`/`encrypted`
   write.

## Consequences

- **Blast radius, stated plainly, and smaller than the PAT model.** The only
  stored secret is the App private key, and — like ADR 0026 — a database dump
  ALONE cannot decrypt it: the KEK lives in the environment, never the database.
  Compromise requires BOTH the DB dump AND the KEK. Beyond that, the credential
  actually used to push is a short-lived, repository-scoped installation token
  minted per operation, so a leaked push token expires in about an hour and grants
  only `contents`/`pull_requests` on one repository — a much smaller standing
  exposure than a long-lived PAT. Revocation is uninstalling the App, not rotating
  a shared secret.
- **Proposal-only boundary UNCHANGED (ADR 0012).** The App is granted only
  `contents:write` + `pull_requests:write`, and the writer still only ever pushes
  a NEW proposal branch and opens a PR; it never merges, approves, writes governed
  context, or runs SQL. Authority remains a human Git merge. Writes appear as
  `hyperset[bot]`.
- **Encryption at rest and App auth are ORTHOGONAL to admin authentication, and do
  not imply it.** The `/admin` write surface still has no real auth beyond
  loopback and the surface gate: a caller who reaches the admin prefix can still
  SET the App id and key. This ADR protects the stored key at rest and shrinks the
  push credential's lifetime; it does not authenticate the admin write path. Real
  admin auth is tracked separately as hy-2nqb; this ADR does not close it and must
  not be read as doing so.
- **New dependency: PyJWT** (RS256 signing; `cryptography` was already present).
  A direct core dependency, like `cryptography` — the github_app path is core
  admin config, not an optional surface.
- `tools_hash` and the MCP trust surface are unaffected — admin config, not a
  served operation.
