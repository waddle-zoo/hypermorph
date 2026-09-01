# 0026: The write-back token may be stored encrypted at rest, keyed from the environment

> **Amended (scope note) by [ADR-0036](0036-bring-your-own-knowledge-graph-authority-adapters.md) (PROPOSED).** This encrypted-at-rest Git write-back token is the Git INSTANCE of a general "authority-backend write credential": secret-by-reference, encrypted-at-rest, fail-closed, minted-not-stored. A non-Git (KG) target brings its own credential under the same floor.

Status: ACCEPTED — the Overseer directed this P1 (2026-08-09) and approved the
design in the hy-up4k dispatch; ships the normal loop (hyperion implements,
critic security-reviews at exact head, oversight merges). This ADR records the
design as built.

Extends ADR 0012 (authority is a human Git merge) and the write-back token model
of hy-eji4. It changes no approval boundary and adds no served operation: the
token is admin CONFIG for the proposal-only writer, not an MCP tool, so
`tools_hash` is unaffected.

## Context

hy-eji4 gave the URL write-back target a token by REFERENCE: the config row
stores the NAME of a server-side secret (an env var), and the value is read from
the environment at propose time. That is the right model for an enterprise with
an external secret manager, but it is friction for a demo or a small deployment
that just wants to paste a GitHub token into the admin Settings UI. The Overseer
directed a second, encrypted-at-rest path, keeping the reference path.

## Decisions

1. **Two token sources, selected by `WritebackConfig.token_source`.**
   - `env_ref` — the hy-eji4 behavior, BYTE-UNCHANGED: `token_ref` is an env var
     NAME, and propose reads the value from the environment.
   - `encrypted` — the admin pastes a token in Settings; the server encrypts it
     and stores only the ciphertext. Both ship; an enterprise keeps `env_ref`.

2. **AES-256-GCM, key from the environment, never the database.** The token is
   encrypted with `AESGCM` (a fresh 12-byte nonce per write) under a
   key-encryption key (KEK) read from `HYPERSET_SECRET_KEY` (base64 of 32 bytes).
   The ciphertext and nonce live in new nullable columns on
   `context_writeback_config`; the KEK is NEVER stored in the database. GCM is
   authenticated, so a tampered ciphertext fails decryption rather than yielding
   garbage.

3. **No silent production key. If `HYPERSET_SECRET_KEY` is unset**, the server
   generates an EPHEMERAL in-memory key once, with a LOUD stderr warning, for
   demo/local use only. Tokens encrypted under it do not survive a restart, by
   design, and the ephemeral key is never persisted — so a real deployment that
   forgets to set the key gets a visible warning and non-durable tokens, never a
   silently written production key.

4. **The token never leaves the server.** The admin write API accepts the raw
   token over the ADMIN surface only (the same surface gate as the rest of the
   write-back config — admin prefix, off the public surface). The status/get
   response returns `{token_source, token_set}` (and, in `env_ref` mode, the env
   var NAME), never the token value and never the ciphertext. The token is never
   logged, never in an HTTP response body, never in the JS bundle, and never in
   an error string.

5. **At propose time the token is decrypted in memory and handed only to the git
   push child's environment**, using the `GIT_CONFIG_*` env mechanism from
   hy-6haz — never a command-line argument — and discarded. `env_ref` uses the
   environment lookup as before.

6. **Fail closed.** A URL target with a missing token, an unset/mismatched KEK,
   or an undecryptable ciphertext REFUSES the propose with a clear error. There
   is no fallback to an unauthenticated push.

## Consequences

- **Blast radius, stated plainly.** A database dump ALONE cannot decrypt a stored
  token: the KEK is not in the database. Compromise of a stored token requires
  BOTH the DB dump AND the KEK (the deployment's environment). The `env_ref`
  path keeps even the ciphertext out of the DB — the token is never there at all
  — which is why it stays the recommended path for an external secret manager.
- **Encryption at rest is ORTHOGONAL to admin authentication, and does not imply
  it.** The `/admin` write surface still has no real auth beyond loopback and the
  surface gate: a caller who reaches the admin prefix can still SET a token.
  Encryption protects the DB-at-rest blast radius, not the write path. Real admin
  auth is tracked separately as hy-2nqb; this ADR does not close it and must not
  be read as doing so.
- `tools_hash` and the MCP trust surface are unaffected — this is admin config,
  not a served operation.
