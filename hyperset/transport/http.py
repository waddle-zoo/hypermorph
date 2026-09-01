"""The HTTP adapter over the v0 operations (hy-oih, hy-x7f).

One POST endpoint per operation, named exactly as the operation is named, so
an agent reading `docs/v0-foundation.md` section 7 can find the endpoint
without a mapping table. The handler decodes JSON, calls `run_operation`, and
writes back what it returned: no endpoint composes, filters, or re-orders a
response, because the MCP adapter serves the same bytes and any reshaping
here would be a second contract.

The standard library is the whole server. The agent operations stay
deterministic; the opt-in operator playground adds the bounded evaluation
harness and its read-only demo SQL tool without expanding the MCP surface.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import sys
import time
import traceback
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse, urlsplit

from hyperset import __version__
from hyperset.bundle import SCHEMA_VERSION
from hyperset.config import context_cache_dir, playground_enabled
from hyperset.context.compare import compare_snapshots
from hyperset.context.history import get_context_history
from hyperset.context.sync import sync_git_context, validate_git_context
from hyperset.observability.correlation import new_correlation_id, set_correlation_id
from hyperset.observability.interaction import (
    opaque_token,
    set_trace_context,
    trace_context_from_headers,
)
from hyperset.ops.diagnostics import DIAGNOSTIC_CLASSES, diagnose
from hyperset.ops.observed_status import read_observed_source_status
from hyperset.ops.provider_probe import probe_providers
from hyperset.ops.readiness import admin_readiness
from hyperset.ops.status import read_git_context
from hyperset.repositories.errors import NotFoundError
from hyperset.repositories.postgres import (
    PostgresAdminAuditRepository,
    PostgresConnectionRepository,
    PostgresContextRepository,
    PostgresGovernedContextRepository,
    PostgresSyncRepository,
    PostgresWritebackConfigRepository,
    select_latest_finished,
)
from hyperset.review.preview import build_preview
from hyperset.security import login
from hyperset.security.authz import authz_enabled
from hyperset.security.oidc import (
    CLIENT_SECRET_ENV,
    TOKEN_ENDPOINT_ENV,
    TokenExchangeError,
    exchange_code_for_id_token,
    principal_from_bearer,
    verify_bearer,
)
from hyperset.security.redaction import (
    URL_USERINFO_G,
    URL_USERINFO_RE,
    redact_free_text_userinfo,
    redact_pointer,
)
from hyperset.transport.operations import (
    DECIDE_CITATION,
    INTERNAL_ERROR,
    INVALID_JSON,
    INVALID_REQUEST,
    METHOD_NOT_ALLOWED,
    OPERATION_SPECS,
    OPERATIONS,
    PROPOSE_CONTEXT_FROM_SEARCH,
    REQUEST_REVIEW_EVIDENCE,
    REQUEST_TOO_LARGE,
    UNKNOWN_ROUTE,
    OperationError,
    _current_governed_definition,
    _decide_citation,
    _iso,
    _load_review_task,
    _principal_workspace,
    _propose_context_from_search,
    _request_review_evidence,
    admin_config_authorization_error,
    authorization_error,
    review_surface_authorization_error,
    run_operation,
    serialize,
)

DEFAULT_HOST = "127.0.0.1"
"""Loopback, because the one deployment that needs every interface asks for it
(hy-voes).

This was `0.0.0.0`, which meant `hyperset serve http` with no flag published an
unauthenticated read API on every interface a laptop happens to be on --
conference wifi, a lab LAN, a coffee shop -- while the container that genuinely
needs all interfaces already passes `--host 0.0.0.0` explicitly in
`docker-compose.yml`. A default nothing depends on was carrying the widest
exposure in the project.

It does not fix a defect in this file; it shrinks who can reach one. Every
envelope failure here -- an unread body, a parked handler thread (hy-6zsk) --
needs someone able to open a socket, and loopback narrows that to processes on
the same host. It makes none of them optional.

WHAT IT BREAKS, stated because the direction matters: `docker run <image> serve
http` with no flag now binds loopback INSIDE the container's own namespace, so a
published port answers nothing. Nothing in this repository does that, and the
failure is a refused connection rather than a service that silently answers
fewer people than intended. Loud is the correct direction for this mistake to
fall."""

DEFAULT_PORT = 8080

# Schemes where a URL can carry credentials in its userinfo (`https://user:token@host`).
# A `git@host:org/repo` scp-style ref parses with no scheme/netloc, so it is not flagged.
_CREDENTIAL_URL_SCHEMES = ("http", "https")

# Userinfo (`user:token@`) inside a `scheme://` authority -- matched TEXTUALLY, before any
# urlsplit, so a URL that urlsplit cannot parse (an unbalanced IPv6 bracket like
# `https://u:tok@[::1/repo`) is still recognised as credential-bearing and never slips past
# the pre-write check or into an audit record with its userinfo intact (hy-gh-75 round 3).
# ONE canonical userinfo class `[^/]*@` now lives in `hyperset.security.redaction` and is
# shared by the pre-write detector, the pointer redactor, the free-text redactor, and the
# operations audit boundary -- no divergent `[^/?#]*@` sibling that leaks a `?`/`#`-embedded
# credential (hq-hnrf #443 round 2). Anchored form detects/redacts a single pointer; global
# form scrubs a free-text field.
_URL_USERINFO_RE = URL_USERINFO_RE
_URL_USERINFO_G = URL_USERINFO_G


def _pointer_has_credentials(value: str) -> bool:
    """True when a repository pointer embeds credentials in its URL userinfo -- a token or
    password in `https://user:token@host/repo`. Such a pointer must never be persisted:
    it lands in the context-source row AND, via the admin audit trail, in a record any
    READ-authorized principal can list (hy-gh-75 round 2). A bare `org/repo`, a local
    path, or an scp-style `git@host:org/repo` ref carries no userinfo and is not flagged.

    FAIL CLOSED: userinfo is detected textually first, so a malformed URL that `urlsplit`
    cannot parse (e.g. `https://u:tok@[::1/repo`, or a multi-`@` / bad-port authority) is
    still flagged rather than waved through (round 3/4). The structured `urlsplit` check
    catches any userinfo the regex would miss."""
    stripped = value.strip()
    if _URL_USERINFO_RE.search(stripped):
        return True
    try:
        parts = urlsplit(stripped)
    except ValueError:
        # Unparseable AND no textual userinfo: no credential to leak, so not flagged here.
        return False
    return parts.scheme in _CREDENTIAL_URL_SCHEMES and bool(parts.username or parts.password)


# The canonical pointer redactor now lives in `hyperset.security.redaction` so the
# operations audit boundary applies the SAME strip (hq-hnrf); re-exported here under its
# established name so this module's callers and tests are unchanged.
_redact_pointer = redact_pointer


# The free-text redactor is the canonical one from `hyperset.security.redaction`, re-exported
# under its established name so this module's callers and tests are unchanged (hq-hnrf).
_redact_free_text_userinfo = redact_free_text_userinfo


# Path per operation, plus one liveness probe for the container healthcheck.
ROUTES = {f"/v0/{name}": name for name in OPERATIONS}
HEALTH_PATH = "/v0/health"
# Read-only operator/UI metadata. This deliberately stays outside `ROUTES`, so
# it is not advertised as an agent operation or exposed through MCP.
CONTEXT_HISTORY_PATH = "/v0/context/history"
# Operator/playground metadata. This is deliberately outside `ROUTES`, so it
# is not an agent-facing operation or exposed through MCP.
PLAYGROUND_STATUS_PATH = "/v0/playground/status"
# A bounded, read-only copy of the deployment's getting-started documentation for the
# in-app Docs surface. It is intentionally outside ROUTES and MCP: docs are UI help, not
# an agent operation.
PLAYGROUND_DOCS_PATH = "/v0/docs/getting-started"
# The flywheel Review surface's read is now the SERVED `list_review_tasks` operation
# (POST /v0/list_review_tasks), so it has no bespoke adapter here (hy-es7z migrated the
# /review UI onto the served review-op paths and removed the thin /v0/review/* adapters).
REVIEW_WHOAMI_PATH = "/v0/review/whoami"
# Ephemeral proposed-context PREVIEW (hy-nauw, V1 gap Reviewer/4): a READ-ONLY render of a
# task's UNAPPROVED draft -- current-vs-proposed meaning, representative questions, and
# deterministic regression checks -- computed in-memory before the reviewer proposes. It is
# NOT SERVING: it writes no governed row, creates no approval, and runs no SQL (ADR 0012). A
# bespoke playground route, so it adds no served OPERATION, is not in ROUTES or on MCP, and
# moves no served contract (SCHEMA_VERSION / tools_hash) -- it stays off the ADR-0025 MCP
# trust surface for that reason.
REVIEW_PREVIEW_PATH = "/v0/review/tasks/preview"
# `WRITEBACK_CONFIG_PATH` configures the write-back TARGET only (hy-8o8m): GET reads it,
# POST sets it -- it approves, merges, and proposes nothing. A bespoke playground route,
# gated behind the playground and not a served OPERATION. (The proposal-only writer is
# now the served `propose_review_to_git` operation, hy-es7z, not an adapter here.)
WRITEBACK_CONFIG_PATH = "/v0/review/writeback-config"
# The admin write-back-TARGET management surface (hq-095h): list/add/update the
# per-domain routing targets, enable/disable one, delete one, and PROBE one for
# reachability (a read-only ls-remote that opens no branch or PR). All are admin
# SETTINGS operations -- served only via the admin api prefix, refused on the
# public playground prefix, and (write paths) admin-authorized server-side. They
# add no served OPERATION, are not in ROUTES, and are not on MCP, so they move no
# served contract (SCHEMA_VERSION / tools_hash).
WRITEBACK_TARGETS_PATH = "/v0/review/writeback-targets"
WRITEBACK_TARGET_ENABLE_PATH = "/v0/review/writeback-targets/enable"
WRITEBACK_TARGET_DELETE_PATH = "/v0/review/writeback-targets/delete"
WRITEBACK_TARGET_TEST_PATH = "/v0/review/writeback-targets/test"
# The interactive-review assist writes (edit an expert's draft, refine it with feedback,
# self-assign) and the proposal-only writer are now the SERVED operations edit_review_draft,
# refine_review_draft, set_review_assignee, and propose_review_to_git (POST /v0/<op>), so
# they have no bespoke adapters here (hy-es7z). Authz (the reviewer allowlist) is enforced
# in run_operation on the served path, not by a playground gate.
# Re-gather a task's observed evidence (hy-to8m, V1 gap Reviewer/3): re-runs the DETERMINISTIC
# step-2 gather and replaces the task's assist `gathered_sources`. A review-AUTHORING mutation
# (REVIEW-gated), but a bespoke playground route -- NOT a served OPERATION, so it adds nothing
# to ROUTES or MCP and moves no served contract. It writes only the UNAPPROVED payload; it
# approves, merges, advances no status, and runs no SQL (ADR 0012).
REVIEW_REQUEST_EVIDENCE_PATH = "/v0/review/tasks/request-evidence"
# Record a human include/exclude/approve/reject on a citation (hy-cpkvu, epic slice 3). A
# review-surface mutation, bespoke HTTP route like request-evidence: NOT a served OPERATION,
# not in ROUTES or on MCP, so it moves no tools_hash and adds no ContextBundle key. It writes
# only the internal citation_decisions AUDIT store; it approves, merges, advances no status,
# and runs no SQL (ADR 0012). REVIEW-gated at the handler AND the service (fail-closed).
REVIEW_CITATION_DECIDE_PATH = "/v0/review/citations/decide"
# Open a PROPOSAL-ONLY write-back review task from search hits + a proposed change (hy-27nl6,
# epic slice 4 CAPSTONE). A review-surface mutation, bespoke HTTP route like decide-citation:
# NOT a served OPERATION, not in ROUTES or on MCP, so it moves no tools_hash and adds no
# ContextBundle key. It creates a human ReviewTask (status open) carrying the proposed change
# + the originating hit citations, routed by domain to the write-back target; it approves,
# writes a governed version, opens a PR, and merges NOTHING (ADR 0012) -- a human still drives
# propose_review_to_git. REVIEW-gated at the handler AND the service (fail-closed).
REVIEW_PROPOSE_FROM_SEARCH_PATH = "/v0/review/proposals/from-search"
# Proposal-lifecycle reconcile (hq-ci92): a bounded, explicit ADMIN call that
# reads a task's proposal PR state from GitHub and, if a HUMAN merged it, re-syncs
# ONLY the routed target's context source. It NEVER merges, approves, or writes a
# governed version (ADR 0012) -- GitHub is the merge authority. Admin-surface only
# (a deployment sync is a config action), refused on the public prefix. It adds no
# served OPERATION, is not in ROUTES or on MCP, and records the lifecycle in the
# task's `proposal_payload` JSONB, so it moves no served contract
# (SCHEMA_VERSION / tools_hash).
REVIEW_TASK_RECONCILE_PATH = "/v0/review/tasks/reconcile"
# Bounded reconcile SWEEP (hq-3ta2): reconcile up to `limit` open-PR tasks in one
# admin call, each isolated. Same admin-only, audited, no-served-contract posture
# as the single reconcile; an externally scheduled bounded call, not a daemon.
REVIEW_TASK_RECONCILE_SWEEP_PATH = "/v0/review/tasks/reconcile-sweep"
HOME_ROOT_PATH = "/"
# The OIDC Authorization-Code + PKCE browser login routes (F4 route-wiring, hy-jyha). Served
# always as auth infrastructure, but INERT when authz is off (loopback dev): `/login` then
# just returns the user where they were, and `/callback` is not a route. When authz is on,
# these are the login path. They add no MCP tool and move no served contract.
LOGIN_PATH = "/login"
CALLBACK_PATH = "/callback"
LOGOUT_PATH = "/logout"
# The admin SETTINGS surface lives at /admin (hy-voyi): the public playground at
# /playground is the only playground, and admin carries no playground segment.
# The two api prefixes stay the surface gate for the write-back target: the write
# is served off /admin/api and refused on /playground/api.
ADMIN_ROOT_PATH = "/admin"
ADMIN_API_PATH = f"{ADMIN_ROOT_PATH}/api"
PLAYGROUND_USER_ROOT_PATH = "/playground"
PLAYGROUND_USER_API_PATH = f"{PLAYGROUND_USER_ROOT_PATH}/api"
# The dedicated reviewer surface (hy-1f96): its own first-class page, not a
# playground tab. It is public and consumes the review ops through the public
# api prefix, so it needs no api prefix of its own -- only the SPA served here.
REVIEW_ROOT_PATH = "/review"
PLAYGROUND_ROOTS = (ADMIN_ROOT_PATH, PLAYGROUND_USER_ROOT_PATH, REVIEW_ROOT_PATH)
PLAYGROUND_API_PATHS = (ADMIN_API_PATH, PLAYGROUND_USER_API_PATH)
# The admin readiness overview (hy-gh-75). Served ONLY on the ADMIN api prefix -- it is an
# operator surface, not a user one, so it is deliberately NOT in `PLAYGROUND_API_PATHS`
# above and is unreachable via `/playground/api`. It is authenticated through the same
# authz gate every governed read uses (`authorization_error`), so on a network deployment
# (where auth is required to start, hy-71mi) it is not answered unauthenticated.
ADMIN_READINESS_PATH = f"{ADMIN_API_PATH}/v0/readiness"
# Admin context-SOURCE management (hy-gh-75): list configured Git context sources, add a
# repo/ref/path source, and sync (validate + fetch) one. ADMIN-surface-only and authz-gated
# like the readiness overview. Per ADR-0012 these manage the Git context POINTER only --
# register a repo/ref/path and snapshot what Git already owns -- and never create, edit, or
# approve governed meaning. The GET is matched at its full admin path (before the `/v0/`
# proxy); the POSTs are matched after the prefix strip, refused on the public surface.
ADMIN_CONTEXT_SOURCES_PATH = f"{ADMIN_API_PATH}/v0/context/sources"
CONTEXT_SOURCES_PATH = "/v0/context/sources"
CONTEXT_SOURCE_SYNC_PATH = "/v0/context/sources/sync"
# The remaining admin context-SOURCE management writes (hq-3fjt): validate a
# source's ref WITHOUT persisting (a dry run that opens no snapshot),
# enable/disable a source (soft, reversible; disabled is excluded from serving),
# and remove a mis-added POINTER that holds no governed history (a source WITH
# snapshots is disable-only, ADR 0012). All admin CONFIGURE-gated, refused on the
# public prefix, and audited on the shared session -- like add/sync. They add no
# served OPERATION and are not on MCP, so they move no served contract.
CONTEXT_SOURCE_VALIDATE_PATH = "/v0/context/sources/validate"
CONTEXT_SOURCE_ENABLE_PATH = "/v0/context/sources/enable"
CONTEXT_SOURCE_REMOVE_PATH = "/v0/context/sources/remove"
# Compare two SERVING commits of a source, and roll a source back to a prior pinned
# commit it already snapshotted (hy-bo5p, V1 gap Admin/4). Compare is read-only; rollback
# RE-PINS `current_snapshot_id` at an existing prior snapshot -- an INTEGRATION action that
# reads no Git, writes no governed row, and creates no approval (ADR 0012), audited on the
# shared session like the other manage writes. Both admin CONFIGURE-gated, refused on the
# public prefix, and off MCP, so they move no served contract / tools_hash.
CONTEXT_SOURCE_COMPARE_PATH = "/v0/context/sources/compare"
CONTEXT_SOURCE_ROLLBACK_PATH = "/v0/context/sources/rollback"
# The admin AUDIT TRAIL (hy-gh-75): a read-only, append-only log of admin config actions.
# Admin-surface-only and authz-gated like the readiness overview.
ADMIN_AUDIT_PATH = f"{ADMIN_API_PATH}/v0/audit"
# The redacted admin AUDIT EXPORT (hy-w9ntg, V1 gap Admin/8): a bounded, workspace-scoped dump
# of the audit trail with every free-text field re-redacted (URL userinfo stripped, defense in
# depth over the already-redacted stored rows). A full export is higher-sensitivity than the
# list, so it is CONFIGURE-gated (not the list's READ gate). Admin-surface-only, off MCP.
ADMIN_AUDIT_EXPORT_PATH = f"{ADMIN_API_PATH}/v0/audit/export"
# The admin write-back-TARGET list (hq-095h): matched at its full admin path (before the
# `/v0/` proxy), authz-gated like the readiness overview. Its POST siblings (add/update,
# enable, delete, test) are matched after the prefix strip and refused on the public surface.
ADMIN_WRITEBACK_TARGETS_PATH = f"{ADMIN_API_PATH}/v0/review/writeback-targets"
# Admin observed-evidence CONNECTION management (hq-jedd): list the configured
# Superset/DataHub/warehouse connections, add/update one (connector_type + display_name +
# non-secret config_ref), enable/disable, remove (only one with NO observed assets;
# disable-only otherwise), and a bounded live PROBE (configured/reachable/fresh, honest
# about degraded). All admin CONFIGURE-gated, refused on the public prefix, mutations
# audited on the shared session. Secrets stay in the server environment; nothing here
# stores or returns a credential. They add no served OPERATION and are not on MCP, so they
# move no served contract (SCHEMA_VERSION / tools_hash). The GET list is matched at its full
# admin path (before the `/v0/` proxy); the POSTs after the prefix strip.
ADMIN_CONNECTIONS_PATH = f"{ADMIN_API_PATH}/v0/connections"
# Per-source observed-status OVERVIEW (hq-hnrf area 2): every configured observed source's
# recorded configured/reachable/fresh rollup, read-only, workspace-scoped.
ADMIN_OBSERVED_STATUS_PATH = f"{ADMIN_API_PATH}/v0/observed-sources/status"
# Bounded LIVE reachability probe of the model/provider components (hy-ng8o7, V1 gap Admin/5):
# OpenAI, analytics-DB, embedding, runtime. The readiness overview reports config
# presence; this actively checks reachability, bounded and fail-safe. It exposes deployment
# config, so it is CONFIGURE-gated (like the connection probe); the reason is redacted at the
# boundary; secrets never leave the server. Admin-surface-only, off MCP.
ADMIN_PROVIDERS_PROBE_PATH = f"{ADMIN_API_PATH}/v0/providers/probe"
# Maintainer DIAGNOSTICS view (hy-bue7r, V1 gap Integrator/3): classifies the standing health
# signals (readiness, observed-status, live provider probes) into the five maintainer failure
# classes -- regression / missing_model / stale_context / connector_outage / invalid_input --
# so an operator sees WHAT KIND of failure this is. CONFIGURE-gated (it live-probes providers
# and exposes deployment health), admin-surface-only, off MCP; free-text redacted.
ADMIN_DIAGNOSTICS_PATH = f"{ADMIN_API_PATH}/v0/diagnostics"
CONNECTIONS_PATH = "/v0/connections"
CONNECTION_ENABLE_PATH = "/v0/connections/enable"
CONNECTION_REMOVE_PATH = "/v0/connections/remove"
CONNECTION_PROBE_PATH = "/v0/connections/probe"

# A context question is a sentence and a plan is a short list. Anything past
# this is a mistake or an attempt to make the server hold a large body in
# memory, and neither deserves a read.
MAX_BODY_BYTES = 1 << 20

# How long a connection may go SILENT before this server gives up on it
# (hy-9sw).
#
# Read that literally, because the weaker claim is the true one: this is not a
# deadline for a request. A socket timeout applies per blocking operation, so
# every `recv` that returns any data resets the clock, and a client dribbling
# one byte every 29 seconds keeps the read looping and the thread parked
# indefinitely. `MAX_BODY_BYTES` bounds how much such a client can send and
# nothing here bounds how long it may take (hy-6zsk). What this closes is the
# measured case: a client that declares a `Content-Length`, sends less, and
# then says nothing at all.
#
# `rfile` is a `BufferedReader`, so `read(n)` returns only on n bytes or EOF,
# and `BaseHTTPRequestHandler` sets no timeout at all -- measured at 2.5s with
# no response and the handler still blocked, against a half-close which
# answered in 0.0s. `ThreadingHTTPServer` takes a thread per connection, so
# repeating that accumulates threads with nothing reclaiming them: one byte per
# connection is enough, which is why a read-only local service still owes this
# bound.
#
# Thirty seconds is chosen against what the server does rather than as a round
# number: no operation here executes SQL or calls a source system, so a
# connection that has said nothing for thirty seconds is not slow work, it is a
# client that stopped sending.
BODY_READ_TIMEOUT_SECONDS = 30

# How much of the body one read may take. Small enough that the deadline is
# checked often, large enough that a normal body is one or two reads: every
# request this server answers is a question and a plan, so the ceiling matters
# far more than the chunk size.
READ_CHUNK_BYTES = 1 << 16

# RFC 7230 3.3.2: Content-Length is 1*DIGIT and nothing else. `int()` is too
# permissive for a framing decision -- it accepts '+0', '-0', ' 12 ', and '1_2' --
# and a value it reads as 0 on a request that actually carries bytes desyncs the
# connection. Anchored digits only, matched against the OWS-stripped value.
_CONTENT_LENGTH_RE = re.compile(r"[0-9]+")


class _Handler(BaseHTTPRequestHandler):
    """Per-request handler. `session_factory` is set on the server."""

    protocol_version = "HTTP/1.1"
    server_version = "hyperset"
    sys_version = ""

    # Applied by `StreamRequestHandler.setup` through `connection.settimeout`,
    # so it covers every blocking read on the connection -- the request line,
    # the headers, and the body (hy-9sw).
    #
    # WHAT IT BOUNDS IS A SILENCE, NOT A REQUEST, and the two read alike at a
    # glance: the clock is per blocking operation and restarts whenever any
    # bytes arrive, so this ends a connection that stops talking and does
    # nothing at all to one that keeps dribbling (hy-6zsk).
    timeout = BODY_READ_TIMEOUT_SECONDS

    # The safe state belongs to the class, not to an override running: if
    # `handle_one_request` is ever bypassed, `_fail` reads False and closes
    # rather than raising `AttributeError` and answering nothing at all.
    _body_consumed = False

    def do_GET(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's contract
        # A GET reads no body on any path, so its success responses (`_respond`,
        # `_respond_html`, `_respond_file`, `_redirect`) never drain one and would
        # keep a connection whose request carried a body -- the leftover bytes are
        # then parsed as the next request (hy-670). `_fail` already closes on the
        # GET failure paths; close here too, from the SAME predicate, so a GET
        # carrying a declared body (or unknown framing) cannot corrupt the next
        # call. A bodyless GET stays clean, so /v0/health keeps its keep-alive.
        self._begin_request()
        if not self._socket_is_clean():
            self.close_connection = True
        path = self._path()
        if path == LOGIN_PATH:
            self._get_login()
            return
        if path == CALLBACK_PATH:
            self._get_callback()
            return
        if path == LOGOUT_PATH:
            self._get_logout()
            return
        # Approved-reviewer gate on the Review SURFACE (hy-mg8p): with the authz gate on,
        # only a caller holding the `review` grant may OPEN the /review page. An
        # unauthenticated or unauthorized browser is sent to /login (deep-linked back to
        # the page it asked for) and never served the SPA shell -- the same `review`
        # decision the review-authoring ops enforce at `run_operation`, so the surface
        # and the ops it drives cannot diverge. Inert with the gate off (loopback dev),
        # so the served bytes are byte-identical to before. Placed BEFORE the page is
        # served, so an unauthorized caller gets a redirect, never the shell.
        if (
            _is_review_surface_page(path)
            and _playground_enabled()
            and review_surface_authorization_error(self._principal()) is not None
        ):
            self._redirect(f"{LOGIN_PATH}?return={quote(path, safe='')}")
            return
        if path == HOME_ROOT_PATH and _playground_enabled():
            self._respond_html(_home_index())
            return
        if path in PLAYGROUND_ROOTS and _playground_enabled():
            self._respond_html(_playground_index().read_bytes())
            return
        if _playground_page(path) and _playground_enabled():
            self._respond_html(_playground_index().read_bytes())
            return
        asset = _playground_asset(path)
        if asset is not None and _playground_enabled():
            self._respond_file(asset)
            return
        if (
            any(path == f"{api_path}/demo/status" for api_path in PLAYGROUND_API_PATHS)
            and _playground_enabled()
        ):
            from playground.ui.app import _demo_status

            try:
                self._respond(200, _demo_status())
            except Exception as exc:  # noqa: BLE001 -- the UI renders source availability
                self._respond(502, {"error": str(exc)})
            return
        if (
            any(path == f"{api_path}{PLAYGROUND_DOCS_PATH}" for api_path in PLAYGROUND_API_PATHS)
            and _playground_enabled()
        ):
            self._get_public_docs()
            return
        if path == ADMIN_READINESS_PATH and _playground_enabled():
            # Matched here, BEFORE the `/v0/` proxy below, because that proxy strips the
            # `/admin/api` prefix and re-dispatches -- a bare `/v0/readiness` has no backend
            # route. Matching the full admin path keeps this an admin-surface-only endpoint.
            self._get_admin_readiness()
            return
        if path == ADMIN_CONTEXT_SOURCES_PATH and _playground_enabled():
            # Admin-surface-only, matched before the `/v0/` proxy (same reason as readiness).
            self._get_context_sources()
            return
        if path == ADMIN_AUDIT_PATH and _playground_enabled():
            self._get_admin_audit()
            return
        if path == ADMIN_AUDIT_EXPORT_PATH and _playground_enabled():
            self._get_admin_audit_export()
            return
        if path == ADMIN_WRITEBACK_TARGETS_PATH and _playground_enabled():
            # Admin-surface-only, matched before the `/v0/` proxy (same reason as readiness).
            self._get_writeback_targets()
            return
        if path == ADMIN_OBSERVED_STATUS_PATH and _playground_enabled():
            self._get_observed_source_status()
            return
        if path == ADMIN_PROVIDERS_PROBE_PATH and _playground_enabled():
            self._get_admin_providers_probe()
            return
        if path == ADMIN_DIAGNOSTICS_PATH and _playground_enabled():
            self._get_admin_diagnostics()
            return
        if path == ADMIN_CONNECTIONS_PATH and _playground_enabled():
            # Admin-surface-only, matched before the `/v0/` proxy (same reason as readiness).
            self._get_connections()
            return
        if (
            any(path.startswith(f"{api_path}/v0/") for api_path in PLAYGROUND_API_PATHS)
            and _playground_enabled()
        ):
            original = self.path
            for api_path in PLAYGROUND_API_PATHS:
                if self.path.startswith(api_path):
                    self.path = self.path.removeprefix(api_path)
                    break
            try:
                self.do_GET()
            finally:
                self.path = original
            return
        if path == HEALTH_PATH:
            self._respond(
                200,
                {
                    "status": "ok",
                    "schema_version": SCHEMA_VERSION,
                    "operations": list(OPERATIONS),
                },
            )
            return
        if path == CONTEXT_HISTORY_PATH:
            self._get_context_history()
            return
        if path == PLAYGROUND_STATUS_PATH:
            self._get_playground_status()
            return
        if path == REVIEW_PREVIEW_PATH and _playground_enabled():
            self._get_review_task_preview()
            return
        if path == REVIEW_WHOAMI_PATH and _playground_enabled():
            self._get_review_whoami()
            return
        if path == WRITEBACK_CONFIG_PATH and _playground_enabled():
            self._get_writeback_config()
            return
        if path in ROUTES:
            self._fail(405, _method_error(path), allow="POST")
            return
        self._fail(404, _route_error(path))

    def _get_context_history(self) -> None:
        # Governed context history is a served READ, so it is gated too -- with the
        # SAME uniform denial `run_operation` raises, decided FIRST so a denied caller
        # learns nothing from the params check or the existence lookup below (an
        # unknown repository/ref/path otherwise answers 400 NotFound and an existing
        # one 200, which would be an existence oracle). Off by default: no gate, no
        # change. This route does not route through `run_operation`, so it carries the
        # gate explicitly rather than growing a second, divergent decision.
        denial = authorization_error(None, {}, self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
        values = {key: query.get(key, [""])[0].strip() for key in ("repository", "ref", "path")}
        missing = [key for key, value in values.items() if not value]
        if missing:
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    (
                        "context history requires repository, ref, and path; "
                        f"missing {', '.join(missing)}"
                    ),
                    recovery=(
                        "use the repository, ref, and path from a catalog entry; "
                        f"GET {CONTEXT_HISTORY_PATH}?repository=...&ref=...&path=..."
                    ),
                ),
            )
            return
        try:
            history = get_context_history(
                session_factory=self.server.session_factory,
                workspace=self._principal_workspace(),
                **values,
            )
        except NotFoundError:
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    "no configured context source matches that repository, ref, and path",
                    recovery=(
                        "use the exact repository, ref, and path returned by list_context_catalog"
                    ),
                ),
            )
            return
        except Exception as exc:  # noqa: BLE001 -- keep the operator route fail-closed
            traceback.print_exc()
            self._fail(
                500,
                OperationError(
                    INTERNAL_ERROR,
                    f"the server could not read context history ({type(exc).__name__})",
                    recovery="retry; if it persists, check the api and postgres logs",
                ),
            )
            return
        history["schema_version"] = SCHEMA_VERSION
        self._respond(200, history)

    def _principal(self):
        """The verified caller behind this request: a `Authorization: Bearer` token OR the
        browser SESSION cookie the F4 login flow set (hy-jyha), or `None`. Both return
        `None` when the authz gate is off (the default), so the value threaded into
        `run_operation` is `None` and the request is answered exactly as today. A bearer
        (agent/gateway) is checked first; a browser with no bearer falls back to its
        session cookie -- both are the same audited verification, never a `Principal` built
        in the transport."""
        bearer = principal_from_bearer(self.headers.get("Authorization"))
        if bearer is not None:
            return bearer
        return login.principal_from_session(self._cookie(login.SESSION_COOKIE_NAME))

    def _principal_workspace(self) -> str:
        """The TENANT/WORKSPACE this request acts in (hq-t6nx, ADR-0037): the verified
        principal's workspace, or the single implicit 'default' when the authz gate is
        off (no principal). Never a wildcard, so every admin read/manage is confined to
        exactly one workspace and a caller can never see another tenant's config."""
        principal = self._principal()
        return principal.workspace if principal is not None else "default"

    # --- F4 login/callback/logout (hy-jyha) ---

    def log_request(self, code: str | int = "-", size: str | int = "-") -> None:
        """Access-log one request, REDACTING the query string of EVERY logged request line so
        an OAuth `code`/`state` (or any other query value) never reaches stderr (hy-jyha,
        ruling hq-l4g2). Redacting the query for ALL routes -- not an auth-path allowlist --
        is what makes this robust: it does not matter whether the path is `/callback`,
        `/callback/`, `/CALLBACK`, `/callback%2F`, a 404, or any future variant that still
        reaches an auth handler; an access log has no need for query strings anyway. The
        method, path, and HTTP version are preserved; dispatch is untouched (round 4)."""
        self.log_message('"%s" %s %s', self._redacted_requestline(), str(code), str(size))

    def _redacted_requestline(self) -> str:
        """`self.requestline` with EVERY `?query` token masked to `?<redacted>`, operating on
        the RAW line so it does not depend on the field count. A fixed 3-field split failed on
        a request line the stdlib router still accepts: `parse_request` splits on ANY run of
        whitespace, so `GET  /callback?code=...&state=... HTTP/1.1` (double space or a tab)
        routes to the callback but has four fields, and the old split-of-3 bailed out and
        logged the raw code/state (round 5). Redacting `?...` up to the next whitespace covers
        single-space, multi-space, tab-separated, and malformed one-field lines alike; the
        path, method, and HTTP version are preserved and dispatch is untouched."""
        return re.sub(r"\?[^\s]*", "?<redacted>", self.requestline)

    def _get_login(self) -> None:
        """Start an OIDC Authorization-Code + PKCE login. Generates PKCE + state + nonce and
        the deep-link return target, signs them into a short-lived HttpOnly login cookie, and
        302s to the IdP. INERT off the authz gate: with authz disabled there is nothing to
        log into, so it just returns the user to the (allow-listed) deep link."""
        query = parse_qs(urlparse(self.path).query)
        return_to = login.safe_return_target(query.get("return", [None])[0])
        if not authz_enabled():
            self._redirect(return_to)
            return
        state = login.new_state()
        nonce = login.new_state()
        verifier = login.new_code_verifier()
        try:
            url = login.authorize_url(
                authorization_endpoint=os.environ.get(login.AUTHORIZATION_ENDPOINT_ENV, "").strip(),
                client_id=os.environ.get(login.CLIENT_ID_ENV, "").strip(),
                redirect_uri=os.environ.get(login.REDIRECT_URI_ENV, "").strip(),
                state=state,
                nonce=nonce,
                challenge=login.code_challenge(verifier),
                scope=os.environ.get(login.SCOPES_ENV, "").strip() or login.DEFAULT_SCOPES,
            )
            cookie = login.login_state_set_cookie(
                login.issue_login_state(
                    state=state, code_verifier=verifier, nonce=nonce, return_to=return_to
                ),
                secure=self._cookie_secure(),
            )
        except login.LoginConfigError as exc:
            self._fail(
                500,
                OperationError(
                    INTERNAL_ERROR,
                    f"login is not configured ({exc})",
                    recovery=(
                        "set HYPERSET_OIDC_AUTHORIZATION_ENDPOINT / CLIENT_ID / REDIRECT_URI "
                        "and HYPERSET_SESSION_SECRET"
                    ),
                ),
            )
            return
        self._redirect(url, status=302, cookies=(cookie,))

    def _get_callback(self) -> None:
        """Finish the login: verify the returned `state` against the signed login cookie,
        exchange the code (with the PKCE verifier) for tokens, VERIFY the id_token, and mint
        a session cookie -- then 303 to the preserved deep link. Fail-closed and uniform: any
        bad state, failed exchange, or unverifiable token redirects to `/?login=failed` with
        NO session and the single-use login cookie cleared, disclosing nothing about which
        step failed. Not a route off the authz gate."""
        if not authz_enabled():
            self._fail(404, _route_error(self._path()))
            return
        secure = self._cookie_secure()
        cleared = login.login_state_clear_cookie(secure=secure)
        failed = ("/?login=failed", {"cookies": (cleared,)})

        login_state = login.read_login_state(self._cookie(login.LOGIN_STATE_COOKIE_NAME))
        query = parse_qs(urlparse(self.path).query)
        code = (query.get("code", [""])[0] or "").strip()
        returned_state = (query.get("state", [""])[0] or "").strip()
        if (
            login_state is None
            or not code
            or not returned_state
            or not hmac.compare_digest(returned_state, login_state.state)
        ):
            self._redirect(failed[0], cookies=failed[1]["cookies"])
            return
        try:
            id_token = exchange_code_for_id_token(
                code=code,
                code_verifier=login_state.code_verifier,
                token_endpoint=os.environ.get(TOKEN_ENDPOINT_ENV, "").strip(),
                client_id=os.environ.get(login.CLIENT_ID_ENV, "").strip(),
                redirect_uri=os.environ.get(login.REDIRECT_URI_ENV, "").strip(),
                client_secret=os.environ.get(CLIENT_SECRET_ENV, "").strip() or None,
            )
            # Bind the verified ID token back to THIS login: the token's nonce claim must
            # equal the nonce minted at /login (carried in the signed login cookie). A
            # replayed or mis-bound token has no matching nonce and verifies to None.
            principal = verify_bearer(id_token, expected_nonce=login_state.nonce)
        except TokenExchangeError:
            self._redirect(failed[0], cookies=failed[1]["cookies"])
            return
        if principal is None:
            self._redirect(failed[0], cookies=failed[1]["cookies"])
            return
        try:
            session = login.issue_session(
                subject=principal.subject,
                issuer=principal.issuer,
                roles=principal.roles,
                workspace=principal.workspace,
            )
        except login.LoginConfigError:
            self._redirect(failed[0], cookies=failed[1]["cookies"])
            return
        self._redirect(
            login_state.return_to,
            cookies=(login.session_set_cookie(session, secure=secure), cleared),
        )

    def _get_logout(self) -> None:
        """End the session: clear the session cookie and 303 to the (allow-listed) return
        target. Idempotent -- clearing an absent cookie is a no-op."""
        query = parse_qs(urlparse(self.path).query)
        target = login.safe_return_target(query.get("return", [None])[0])
        self._redirect(target, cookies=(login.session_clear_cookie(secure=self._cookie_secure()),))

    def _get_review_task_preview(self) -> None:
        """Ephemeral proposed-context PREVIEW for one review task (hy-nauw). READ-ONLY: it
        loads the task's UNAPPROVED draft and the governed CURRENT meaning it already reads,
        and renders current-vs-proposed, representative questions, and regression checks
        in-memory. It writes no governed row, creates no approval, and runs no SQL beyond the
        governed-context read (ADR 0012); `not_serving: True` rides on the payload.

        It reveals a review task's content, so it carries the SAME uniform READ gate the review
        reads enforce via `run_operation` -- decided FIRST, before the task is loaded, so a
        denied caller learns nothing from an existence lookup. This route does not go through
        `run_operation`, so it names the one shared gate explicitly (hy-nauw), never a second."""
        denial = authorization_error(None, {}, self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
        task_id = (query.get("task_id", [""])[0] or "").strip()
        if not task_id:
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    "preview needs a task_id",
                    recovery="GET /v0/review/tasks/preview?task_id=<id from the review surface>",
                ),
            )
            return
        session_factory = self.server.session_factory
        try:
            task = _load_review_task(
                task_id,
                session_factory=session_factory,
                workspace=self._principal_workspace(),
            )
        except OperationError as exc:
            self._fail(500 if exc.code == INTERNAL_ERROR else 400, exc)
            return
        governed_repo = PostgresGovernedContextRepository(session_factory)
        current = _current_governed_definition(
            task, governed_repo=governed_repo, workspace=self._principal_workspace()
        )
        self._respond(200, build_preview(task, current_meaning=current))

    def _get_review_whoami(self) -> None:
        """The CALLER'S OWN server-derived identity for the reviewer UI (hy-q7pth round 2).

        The opaque `subject@issuer` of the VERIFIED principal, or `null` when there is no
        verified identity (the authz gate off, or no bearer/session). The reviewer UI uses
        THIS -- read fresh from the server, never a persisted client value -- as the sole
        authority for 'assigned to me' and the 'assigned to you' label. So a stale login,
        an anonymous (null) caller, or edited client storage can never forge ownership: a
        re-login yields the new identity, and `null` disables the filter rather than
        treating every anonymous-owned task as mine. It is the SAME identity `set_review_
        assignee` self-computes, and it exposes only the caller's own identity (which the
        caller already knows), so it discloses nothing and is not a governed read.
        """
        principal = self._principal()
        identity = f"{principal.subject}@{principal.issuer}" if principal is not None else None
        self._respond(200, {"identity": identity})

    def _json_body(self) -> dict | None:
        """Read and decode a JSON object body, or fail (and return None)."""
        body = self._read_body()
        if body is None:
            return None
        try:
            params = json.loads(body) if body.strip() else {}
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._fail(400, _decode_error("request", exc))
            return None
        if not isinstance(params, dict):
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    "the request body must be a JSON object",
                    recovery="send a JSON object",
                ),
            )
            return None
        return params

    def _get_admin_readiness(self) -> None:
        """The admin readiness overview (hy-gh-75): a bounded, read-only snapshot of each
        component's status. Gated by the SAME fail-closed authz decision as every governed
        read -- off by default (no gate, answered locally), and on a network deployment
        (where auth is required to start, hy-71mi) it is refused without a verified
        principal. It surfaces no secret value. This route does not go through
        `run_operation`, so it carries the gate explicitly, exactly like context history."""
        denial = authorization_error(None, {}, self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        report = admin_readiness(self.server.session_factory, env=os.environ)
        self._respond(200, {"schema_version": SCHEMA_VERSION, **report})

    def _get_context_sources(self) -> None:
        """List the configured Git context sources (hy-gh-75): source id, repo/ref/path,
        the serving commit, the last sync attempt, the validation state, and the estate
        DOMAIN-CONFLICT state (hq-3fjt). Admin-surface-only and gated by the ADMIN
        CONFIGURE authz -- the SAME boundary as the source mutations, NOT the generic
        governed-READ gate (hq-3fjt, matching the write-back-targets ruling hq-1aap): the
        list exposes deployment config (target repositories and ref/path pointers), so a
        generic READ principal must not read it. It reports Git commit metadata only --
        never a secret value."""
        denial = admin_config_authorization_error(self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        rows = read_git_context(self.server.session_factory, workspace=self._principal_workspace())
        # Surface the SAME estate ambiguity the resolver refuses on (hy-gh-282): a domain
        # claimed by more than one ENABLED serving source. Grouped exactly as the resolver
        # groups (`bundle.resolver`), so the admin sees the conflict the resolve path would
        # -- the estate is never silently merged. A disabled source is not an active
        # claimant, so it neither causes nor is flagged for a conflict.
        claimants_by_domain: dict[str, list[str]] = {}
        for row in rows:
            if row.enabled and row.domain and row.commit_sha:
                claimants_by_domain.setdefault(row.domain, []).append(row.source_id)
        conflicted = {d: ids for d, ids in claimants_by_domain.items() if len(ids) > 1}
        self._respond(
            200,
            {
                "schema_version": SCHEMA_VERSION,
                "sources": [
                    _context_source_view(
                        row,
                        conflicting_source_ids=[
                            other
                            for other in conflicted.get(row.domain or "", [])
                            if other != row.source_id
                        ]
                        if (row.enabled and row.domain and row.commit_sha)
                        else [],
                    )
                    for row in rows
                ],
            },
        )

    def _get_admin_audit(self) -> None:
        """The admin AUDIT TRAIL (hy-gh-75): a read-only, newest-first list of admin config
        actions (who / what / target / when / result). Admin-surface-only and gated by the
        same fail-closed authz decision as every governed read. Carries no secret value."""
        denial = authorization_error(None, {}, self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        entries = PostgresAdminAuditRepository(self.server.session_factory).list(
            workspace=self._principal_workspace()
        )
        self._respond(
            200,
            {
                "schema_version": SCHEMA_VERSION,
                "entries": [_audit_view(entry) for entry in entries],
            },
        )

    def _get_admin_audit_export(self) -> None:
        """A REDACTED export of the admin audit trail (hy-w9ntg, V1 gap Admin/8).

        The workspace-scoped trail with every free-text field re-redacted (URL userinfo
        stripped via the canonical `security/redaction`), so a legacy or unredacted row can
        never surface a credential on the way out. A full export is higher-sensitivity than
        the list, so it is CONFIGURE-gated (not the list's READ gate) -- decided first, so a
        denied caller learns nothing. Read-only: it writes nothing and carries no secret."""
        denial = admin_config_authorization_error(self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        entries = PostgresAdminAuditRepository(self.server.session_factory).list(
            workspace=self._principal_workspace()
        )
        self._respond(
            200,
            {
                "schema_version": SCHEMA_VERSION,
                "export": "admin_audit",
                "records": [_audit_export_view(entry) for entry in entries],
            },
        )

    def _get_observed_source_status(self) -> None:
        """Per-source observed-status OVERVIEW (hq-hnrf area 2): every configured
        observed-evidence source's recorded configured/reachable/fresh rollup, with impact
        and recovery. Read-only and RECORDED (no live probe -- the on-demand per-connection
        probe route is the deep check). Exposes deployment config (connection identities +
        health), so it is CONFIGURE-gated like the connections list, and workspace-scoped so
        a tenant's admin sees only its own sources. Carries no secret."""
        denial = admin_config_authorization_error(self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        statuses = read_observed_source_status(
            self.server.session_factory, workspace=self._principal_workspace()
        )
        self._respond(
            200,
            {
                "schema_version": SCHEMA_VERSION,
                "sources": [_observed_status_view(status) for status in statuses],
            },
        )

    def _get_admin_providers_probe(self) -> None:
        """Bounded LIVE reachability probe of the model/provider components (hy-ng8o7): OpenAI,
        analytics-DB, embedding, runtime. Unlike the readiness overview (config
        presence), this actively checks reachability -- bounded per component and fail-safe
        (never hangs or raises; an unconfigured component makes no network call). CONFIGURE-
        gated (it exposes deployment config), decided first. Each `reason` is redacted at this
        boundary; no secret is ever in a probe result."""
        denial = admin_config_authorization_error(self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        self._respond(
            200,
            {
                "schema_version": SCHEMA_VERSION,
                "providers": [_provider_probe_view(probe) for probe in probe_providers()],
            },
        )

    def _get_admin_diagnostics(self) -> None:
        """Maintainer DIAGNOSTICS (hy-bue7r): classify the standing health signals -- the
        readiness overview, the recorded observed-source status, and live provider probes --
        into the five named failure classes, so an operator sees WHAT KIND of failure this is
        and what to do. CONFIGURE-gated (it live-probes and exposes deployment health), decided
        first; admin-surface-only; off MCP. Free text is redacted at this boundary."""
        denial = admin_config_authorization_error(self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        workspace = self._principal_workspace()
        readiness = admin_readiness(self.server.session_factory)
        observed = read_observed_source_status(self.server.session_factory, workspace=workspace)
        rows = diagnose(
            readiness_components=readiness.get("components", []),
            observed_sources=[status.as_dict() for status in observed],
            provider_probes=[probe.as_dict() for probe in probe_providers()],
        )
        counts = {klass: 0 for klass in DIAGNOSTIC_CLASSES}
        for row in rows:
            counts[row.diagnostic_class] += 1
        self._respond(
            200,
            {
                "schema_version": SCHEMA_VERSION,
                "counts": counts,
                "diagnostics": [_diagnosis_view(row) for row in rows],
            },
        )

    def _audit_actor(self) -> tuple[str, str | None]:
        """The `(subject, issuer)` to attribute an admin action to: the verified principal,
        or `("anonymous", None)` when the gate is off (a loopback dev run)."""
        principal = self._principal()
        if principal is None:
            return "anonymous", None
        return principal.subject, principal.issuer

    def _audit(
        self,
        *,
        action: str,
        result: str = "ok",
        target: str | None = None,
        detail: str | None = None,
    ) -> bool:
        """Append one admin-audit entry for the current actor -- MANDATORY, not best-effort
        (hy-gh-75 round 2). The trail is not omittable at its own failure mode: if the append
        raises (audit store down), this emits a 500 and returns False, so the caller rejects
        the operation rather than reporting a success that left no audit row. Returns True on
        success. Used by the sync path, whose snapshot is immutable and idempotent, so a
        rejected-then-retried sync converges with an audit row; the add/writeback paths couple
        the append to their mutation in one transaction instead (see below). Never records a
        secret -- any URL userinfo in `detail` is redacted."""
        actor, issuer = self._audit_actor()
        try:
            PostgresAdminAuditRepository(self.server.session_factory).record(
                actor=actor,
                actor_issuer=issuer,
                workspace=self._principal_workspace(),
                action=action,
                target=target,
                result=result,
                detail=_redact_pointer(detail) if detail else detail,
            )
        except Exception as exc:  # noqa: BLE001 -- turn a failed append into a rejected op
            print(
                f"WARNING: admin audit append failed for {action!r}: {type(exc).__name__}",
                file=sys.stderr,
            )
            self._fail(
                500,
                OperationError(
                    INTERNAL_ERROR,
                    "the action was not recorded to the admin audit trail, so it was "
                    "rejected; the operation is idempotent, so retry once the audit "
                    "store is reachable",
                    recovery="retry the request once the audit store is reachable",
                ),
            )
            return False
        return True

    def _post_context_source_add(self) -> None:
        """Register a Git context SOURCE -- a repo/ref/path POINTER (ADR 0012). It creates no
        governed meaning and approves nothing; it records where to snapshot from. It is a
        deployment-CONFIG write, so it is gated by the admin `configure` grant (admin-only,
        hy-2nqb/#416 precedent), not the generic read gate -- admin-surface-only."""
        denial = admin_config_authorization_error(self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        params = self._json_body()
        if params is None:
            return
        repository = str(params.get("repository", "")).strip()
        path = str(params.get("path", "")).strip()
        ref = str(params.get("ref", "") or "main").strip()
        if not repository or not path:
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    "a context source needs repository and path (ref defaults to main)",
                    recovery="POST { repository, path, ref?, display_name? }",
                ),
            )
            return
        if _pointer_has_credentials(repository):
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    "the repository pointer must not embed credentials in its URL; a token "
                    "or password there would be persisted and shown in the admin audit trail",
                    recovery="remove the user:token@ from the URL and supply the token via "
                    "the write-back token source, not the repository pointer",
                ),
            )
            return
        display_name = str(params.get("display_name", "")).strip() or None
        # Couple the register and its audit append in ONE transaction: if either fails, both
        # roll back, so no source is registered without an audit row (hy-gh-75 round 2).
        actor, issuer = self._audit_actor()
        session_factory = self.server.session_factory
        try:
            with session_factory() as session, session.begin():
                source = PostgresContextRepository(session_factory).register_source(
                    repository=repository,
                    ref=ref,
                    path=path,
                    display_name=display_name,
                    workspace=self._principal_workspace(),
                    session=session,
                )
                PostgresAdminAuditRepository(session_factory).record(
                    actor=actor,
                    actor_issuer=issuer,
                    workspace=self._principal_workspace(),
                    action="context_source.add",
                    target=source.id,
                    result="ok",
                    detail=_redact_pointer(f"{repository}@{ref}:{path}"),
                    session=session,
                )
        except Exception as exc:  # noqa: BLE001 -- an unaudited mutation must not persist
            print(
                f"WARNING: context_source.add rolled back: {type(exc).__name__}",
                file=sys.stderr,
            )
            self._fail(
                500,
                OperationError(
                    INTERNAL_ERROR,
                    "the context source could not be registered together with its audit "
                    "record, so nothing was written; retry",
                    recovery="retry the request once the datastore is reachable",
                ),
            )
            return
        self._respond(
            200, {"schema_version": SCHEMA_VERSION, "source": _source_record_view(source)}
        )

    def _post_context_source_sync(self) -> None:
        """Sync (VALIDATE + fetch) a registered source (hy-gh-75): resolve the ref, read and
        hash the content, and persist an immutable snapshot -- or record the failure. A
        failed read leaves the previous snapshot serving. Reuses the same `sync_git_context`
        the CLI runs. A deployment-CONFIG write, gated by the admin `configure` grant
        (admin-only, hy-2nqb/#416 precedent), admin-surface-only. Snapshots what Git owns;
        approves nothing (ADR 0012). The snapshot/status write and its audit append are
        COUPLED in ONE transaction (hy-oq1y4, mirroring the Validate fix #446): if the audit
        fails, the snapshot and the `last_attempt_*` write roll back too, so a governed
        snapshot is never persisted without an audit row -- the reconcile/writeback coupling,
        not a mandatory-but-separate append."""
        denial = admin_config_authorization_error(self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        params = self._json_body()
        if params is None:
            return
        source_id = str(params.get("source_id", "")).strip()
        if not source_id:
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    "sync needs a source_id",
                    recovery="POST { source_id } for a source from GET context/sources",
                ),
            )
            return
        cache_dir = context_cache_dir()
        actor, issuer = self._audit_actor()
        workspace = self._principal_workspace()
        try:
            # Workspace-scoped (hq-t6nx): syncing snapshots governed context, so a caller
            # must not sync another tenant's source; `sync_git_context` resolves it within
            # this workspace, so a cross-workspace source is NotFound before any read. The
            # Git read runs inside `sync_git_context`; its snapshot/`last_attempt_*` writes
            # are made in THIS session and committed together with the audit row below, so a
            # failed audit rolls the snapshot and the status write back.
            with self.server.session_factory() as session, session.begin():
                result = sync_git_context(
                    source_id=source_id,
                    session_factory=self.server.session_factory,
                    cache_dir=cache_dir,
                    workspace=workspace,
                    session=session,
                )
                PostgresAdminAuditRepository(self.server.session_factory).record(
                    actor=actor,
                    actor_issuer=issuer,
                    workspace=workspace,
                    action="context_source.sync",
                    target=source_id,
                    result="ok" if result.ok else "error",
                    detail=result.status,
                    session=session,
                )
        except NotFoundError as exc:
            # The op already failed and nothing was recorded; note the attempt best-effort (a
            # failed audit here cannot make an already-rejected op any worse) and return 400.
            self._audit(
                action="context_source.sync", target=source_id, result="error", detail="not found"
            )
            self._fail(
                400, OperationError(INVALID_REQUEST, str(exc), recovery="add the source first")
            )
            return
        except Exception as exc:  # noqa: BLE001 -- a failed snapshot/status write or audit rejects
            print(
                f"WARNING: admin audit append failed for 'context_source.sync': "
                f"{type(exc).__name__}",
                file=sys.stderr,
            )
            self._fail(
                500,
                OperationError(
                    INTERNAL_ERROR,
                    "the sync was not recorded to the admin audit trail, so it was rejected "
                    "and nothing was changed; the operation is idempotent, so retry once the "
                    "audit store is reachable",
                    recovery="retry the request once the audit store is reachable",
                ),
            )
            return
        self._respond(200, {"schema_version": SCHEMA_VERSION, "result": _sync_result_view(result)})

    def _post_review_task_reconcile(self) -> None:
        """Reconcile a review task's proposal PR against GitHub (hq-ci92). ADMIN,
        read-then-record: it READS whether a HUMAN merged the PR and, if so,
        re-syncs ONLY the routed target's context source (matched by the target's
        exact identity, so no other target is touched). It NEVER merges, approves,
        or writes a governed version -- GitHub is the merge authority (ADR 0012).
        The lifecycle is recorded in the task's `proposal_payload` JSONB, coupled
        with its audit in one transaction, and surfaces via the existing review
        read; it moves no served contract."""
        denial = admin_config_authorization_error(self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        params = self._json_body()
        if params is None:
            return
        task_id = str(params.get("task_id", "")).strip()
        if not task_id:
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    "reconcile needs a task_id",
                    recovery="POST { task_id } for a task from the review surface",
                ),
            )
            return
        from hyperset.flywheel import git_pr
        from hyperset.flywheel.lifecycle import reconcile_and_record

        cache_dir = context_cache_dir()
        # Reconcile AND record in ONE transaction (hq-ci92 round 2, #421/#428
        # discipline): reconcile_and_record threads the merge re-sync's snapshot, the
        # lifecycle write, and both audit rows through a single session, so if the
        # audit append fails EVERYTHING rolls back -- the source snapshot included --
        # honouring the "nothing recorded" contract. Shared with the sweep (hq-3ta2).
        actor, issuer = self._audit_actor()
        try:
            lifecycle = reconcile_and_record(
                task_id=task_id,
                session_factory=self.server.session_factory,
                cache_dir=cache_dir,
                read_pr_state=git_pr.read_pr_state,
                actor=actor,
                issuer=issuer,
                workspace=self._principal_workspace(),
            )
        except NotFoundError as exc:
            self._audit(
                action="review_task.reconcile", target=task_id, result="error", detail="not found"
            )
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST, str(exc), recovery="use a task id from the review surface"
                ),
            )
            return
        except Exception as exc:  # noqa: BLE001 -- an unaudited reconcile must not persist
            print(
                f"WARNING: review_task.reconcile rolled back: {type(exc).__name__}",
                file=sys.stderr,
            )
            self._fail(
                500,
                OperationError(
                    INTERNAL_ERROR,
                    "the reconcile could not be recorded together with its audit, so nothing "
                    "was recorded; retry",
                    recovery="retry once the datastore is reachable",
                ),
            )
            return
        self._respond(200, {"schema_version": SCHEMA_VERSION, "lifecycle": lifecycle})

    def _post_review_task_reconcile_sweep(self) -> None:
        """Bounded reconcile SWEEP over open-PR tasks (hq-3ta2). ADMIN,
        CONFIGURE-gated, audited: reconciles up to `limit` tasks whose PR is still
        open (oldest-first), EACH in its own isolated transaction, and returns a
        per-task summary. One task's failure never aborts the sweep or corrupts
        another task. It NEVER merges or approves (ADR 0012), adds no served
        OPERATION, and records lifecycle in `proposal_payload` -- so it moves no
        served contract. An externally scheduled bounded call, not a daemon."""
        denial = admin_config_authorization_error(self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        params = self._json_body()
        if params is None:
            return
        from hyperset.flywheel import git_pr
        from hyperset.flywheel.lifecycle import (
            DEFAULT_SWEEP_LIMIT,
            SweepLimitError,
            reconcile_open_proposals,
        )

        # ONE shared validator (hq-3ta2 #437 round 2): reconcile_open_proposals
        # validates `limit`, so the CLI enforces the same bounds -- this route only
        # maps the shared error to a 400.
        cache_dir = context_cache_dir()
        actor, issuer = self._audit_actor()
        try:
            summary = reconcile_open_proposals(
                session_factory=self.server.session_factory,
                cache_dir=cache_dir,
                read_pr_state=git_pr.read_pr_state,
                actor=actor,
                issuer=issuer,
                limit=params.get("limit", DEFAULT_SWEEP_LIMIT),
                workspace=self._principal_workspace(),
            )
        except SweepLimitError as exc:
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    str(exc),
                    recovery="send a limit in range, or omit it for the default",
                ),
            )
            return
        # The sweep-level audit is MANDATORY, not best-effort (hq-3ta2 #437 round 2,
        # same class as #421): a successful admin sweep -- including a zero-task one
        # -- must leave a summary audit row. `_audit` turns a failed append into a
        # 500 and returns False, so a swallowed audit can never report 200. The
        # per-task mutations were already audited individually in reconcile_and_record.
        if not self._audit(
            action="review_task.reconcile_sweep",
            result="ok",
            detail=f"count={summary['count']}",
        ):
            return
        self._respond(200, {"schema_version": SCHEMA_VERSION, "sweep": summary})

    def _context_source_id_param(self) -> str | None:
        """Read + validate the `source_id` body param for a manage action (hq-3fjt),
        AFTER the admin CONFIGURE gate. Returns the id, or None after sending the 400
        so the enable/validate/remove handlers share one shape."""
        params = self._json_body()
        if params is None:
            return None
        source_id = str(params.get("source_id", "")).strip()
        if not source_id:
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    "this action needs a source_id",
                    recovery="POST { source_id } for a source from GET context/sources",
                ),
            )
            return None
        return source_id

    def _post_context_source_validate(self) -> None:
        """VALIDATE a source's configured ref live, WITHOUT persisting a new snapshot
        (hq-3fjt): reads the ref, parses the manifest, and checks estate placement (domain
        collision + hierarchy) with the SAME primitives as sync. It writes NO new governed
        snapshot -- the last valid context keeps serving -- but it DOES record the outcome of
        this live attempt through the same `last_attempt_*` status path sync uses, so the
        admin card and the readiness overview reflect what a live validation just found rather
        than a stale green (hy-ppufd). A deployment-CONFIG action, gated by the admin
        `configure` grant and admin-surface-only. Because it now records an authority-status
        change, the status write and its audit append are COUPLED in ONE transaction (#446
        round 2): if the audit fails, the status write rolls back too, so `last_attempt_*` is
        never left changed without an audit row -- the same atomic coupling the reconcile and
        writeback paths use, not the sync path's mandatory-but-separate append."""
        denial = admin_config_authorization_error(self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        source_id = self._context_source_id_param()
        if source_id is None:
            return
        cache_dir = context_cache_dir()
        actor, issuer = self._audit_actor()
        workspace = self._principal_workspace()
        try:
            # Workspace-scoped (hq-t6nx): a caller must not validate another tenant's source
            # (it reads the configured remote); cross-workspace is NotFound. The Git read runs
            # inside `validate_git_context`; its `last_attempt_*` write is made in THIS session
            # and committed together with the audit row below, so a failed audit append rolls
            # the status write back -- an authority-status change is never persisted unaudited.
            with self.server.session_factory() as session, session.begin():
                result = validate_git_context(
                    source_id=source_id,
                    session_factory=self.server.session_factory,
                    cache_dir=cache_dir,
                    workspace=workspace,
                    session=session,
                )
                PostgresAdminAuditRepository(self.server.session_factory).record(
                    actor=actor,
                    actor_issuer=issuer,
                    workspace=workspace,
                    action="context_source.validate",
                    target=source_id,
                    result="ok" if result.ok else "error",
                    detail=result.status,
                    session=session,
                )
        except NotFoundError as exc:
            self._fail(
                400, OperationError(INVALID_REQUEST, str(exc), recovery="add the source first")
            )
            return
        except Exception as exc:  # noqa: BLE001 -- a failed status write or audit append rejects
            print(
                f"WARNING: admin audit append failed for 'context_source.validate': "
                f"{type(exc).__name__}",
                file=sys.stderr,
            )
            self._fail(
                500,
                OperationError(
                    INTERNAL_ERROR,
                    "the action was not recorded to the admin audit trail, so it was "
                    "rejected and nothing was changed; the operation is idempotent, so retry "
                    "once the audit store is reachable",
                    recovery="retry the request once the audit store is reachable",
                ),
            )
            return
        self._respond(200, {"schema_version": SCHEMA_VERSION, "result": _sync_result_view(result)})

    def _post_context_source_enable(self) -> None:
        """Enable or disable a source (hq-3fjt). Soft and reversible: a DISABLED source is
        excluded from catalog and resolve (both filter `enabled`) without deleting any
        governed history (ADR 0012). Enabling REFUSES a domain collision -- another enabled
        source already claims this source's domain -- exactly like the CLI `enable`, so the
        estate is never silently made ambiguous. Admin CONFIGURE-gated; the state change and
        its audit append are coupled in one transaction."""
        denial = admin_config_authorization_error(self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        params = self._json_body()
        if params is None:
            return
        source_id = str(params.get("source_id", "")).strip()
        if not source_id:
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    "this action needs a source_id",
                    recovery="POST { source_id, enabled } for a source from GET context/sources",
                ),
            )
            return
        enabled = bool(params.get("enabled"))
        workspace = self._principal_workspace()
        session_factory = self.server.session_factory
        repository = PostgresContextRepository(session_factory)
        try:
            source = repository.get_source(source_id, workspace=workspace)
        except NotFoundError as exc:
            self._fail(
                400, OperationError(INVALID_REQUEST, str(exc), recovery="add the source first")
            )
            return
        # Collision refusal on ENABLE, mirroring the CLI (hy-gh-282): re-enabling must not
        # silently re-create the ambiguity a disable resolved. Surfaced, not merged.
        if enabled and source.current_snapshot is not None:
            claimant = repository.source_claiming_domain(
                source.current_snapshot.domain, exclude_source_id=source_id, workspace=workspace
            )
            if claimant is not None:
                self._fail(
                    400,
                    OperationError(
                        INVALID_REQUEST,
                        f"cannot enable {source_id}: the domain "
                        f"{source.current_snapshot.domain!r} is already claimed by enabled "
                        f"source {claimant.id} ({claimant.repository}@{claimant.ref}:"
                        f"{claimant.path}); an estate serves one source per domain",
                        recovery="disable that source first, or change one manifest's domain",
                    ),
                )
                return
        actor, issuer = self._audit_actor()
        saved = None
        try:
            with session_factory() as session, session.begin():
                repo = PostgresContextRepository(session_factory)
                saved = repo.set_enabled(
                    source_id, enabled=enabled, workspace=workspace, session=session
                )
                PostgresAdminAuditRepository(session_factory).record(
                    actor=actor,
                    actor_issuer=issuer,
                    workspace=self._principal_workspace(),
                    action="context_source.enable" if enabled else "context_source.disable",
                    target=source_id,
                    result="ok",
                    detail=f"enabled={enabled}",
                    session=session,
                )
        except Exception as exc:  # noqa: BLE001 -- an unaudited mutation must not persist
            print(
                f"WARNING: context_source.enable rolled back: {type(exc).__name__}",
                file=sys.stderr,
            )
            self._fail(
                500,
                OperationError(
                    INTERNAL_ERROR,
                    "the source state could not be saved together with its audit record",
                    recovery="retry the request once the datastore is reachable",
                ),
            )
            return
        self._respond(200, {"schema_version": SCHEMA_VERSION, "source": _source_record_view(saved)})

    def _post_context_source_remove(self) -> None:
        """Remove a context-source POINTER that holds NO governed history (hq-3fjt).

        ADR 0012 keeps immutable snapshots for replay/comparison, so removal is
        NON-DESTRUCTIVE of governed history: a source with >0 snapshots is
        disable-only and this refuses it (pointing at disable); only a mis-added
        pointer that never produced a snapshot is deletable. Admin CONFIGURE-gated;
        the delete and its audit append are coupled in one transaction."""
        denial = admin_config_authorization_error(self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        source_id = self._context_source_id_param()
        if source_id is None:
            return
        session_factory = self.server.session_factory
        repository = PostgresContextRepository(session_factory)
        try:
            repository.get_source(source_id, workspace=self._principal_workspace())
        except NotFoundError as exc:
            self._fail(400, OperationError(INVALID_REQUEST, str(exc), recovery="nothing to remove"))
            return
        if repository.snapshot_count(source_id) > 0:
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    f"context source {source_id} holds governed snapshots, so it cannot be "
                    "removed (immutable history is kept for replay, ADR 0012)",
                    recovery=(
                        "disable it instead with the enable/disable action -- a disabled "
                        "source is excluded from serving but its history is preserved"
                    ),
                ),
            )
            return
        actor, issuer = self._audit_actor()
        try:
            with session_factory() as session, session.begin():
                PostgresContextRepository(session_factory).delete_source(source_id, session=session)
                PostgresAdminAuditRepository(session_factory).record(
                    actor=actor,
                    actor_issuer=issuer,
                    workspace=self._principal_workspace(),
                    action="context_source.remove",
                    target=source_id,
                    result="ok",
                    detail="removed (no governed history)",
                    session=session,
                )
        except Exception as exc:  # noqa: BLE001 -- an unaudited mutation must not persist
            print(
                f"WARNING: context_source.remove rolled back: {type(exc).__name__}",
                file=sys.stderr,
            )
            self._fail(
                500,
                OperationError(
                    INTERNAL_ERROR,
                    "the source could not be removed together with its audit record",
                    recovery="retry the request once the datastore is reachable",
                ),
            )
            return
        self._respond(200, {"schema_version": SCHEMA_VERSION, "removed": source_id})

    def _post_context_source_compare(self) -> None:
        """Compare TWO serving commits of a source (hy-bo5p, V1 gap Admin/4). READ-ONLY: it
        reads two already-persisted immutable snapshots from THIS source's history (resolved by
        commit SHA, fail-closed if either is absent) and returns their semantic delta -- the
        content-hash `identical` flag plus the added/changed/removed sections of the normalized
        definition (`compare_snapshots`). It opens NO Git read, writes nothing, and touches no
        governed row; an operator uses it to decide whether to roll back. Admin CONFIGURE-gated
        (it exposes governed context content), admin-surface-only, and off MCP -- no served
        contract moves. Workspace-scoped (hq-t6nx): a caller compares only its own source."""
        denial = admin_config_authorization_error(self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        params = self._json_body()
        if params is None:
            return
        source_id = str(params.get("source_id", "")).strip()
        base_commit = str(params.get("base_commit", "")).strip()
        target_commit = str(params.get("target_commit", "")).strip()
        if not source_id or not base_commit or not target_commit:
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    "compare needs a source_id, base_commit, and target_commit",
                    recovery="POST { source_id, base_commit, target_commit } for two commits "
                    "from GET context/sources history",
                ),
            )
            return
        repository = PostgresContextRepository(self.server.session_factory)
        try:
            repository.get_source(source_id, workspace=self._principal_workspace())
        except NotFoundError as exc:
            self._fail(
                400, OperationError(INVALID_REQUEST, str(exc), recovery="add the source first")
            )
            return
        by_commit = {snapshot.commit_sha: snapshot for snapshot in repository.history(source_id)}
        missing = [commit for commit in (base_commit, target_commit) if commit not in by_commit]
        if missing:
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    f"context source {source_id} has no snapshot at commit(s) {', '.join(missing)}",
                    recovery="choose two commits this source has snapshotted (GET the history)",
                ),
            )
            return
        result = compare_snapshots(by_commit[base_commit], by_commit[target_commit])
        self._respond(200, {"schema_version": SCHEMA_VERSION, "comparison": result})

    def _post_context_source_rollback(self) -> None:
        """Roll a source's SERVING snapshot back to a prior pinned commit (hy-bo5p, V1 gap
        Admin/4). An INTEGRATION action, not governed authorship (ADR 0012): it RE-PINS
        `current_snapshot_id` at a snapshot this source ALREADY produced -- it reads no Git,
        creates no new snapshot, writes no governed_context row, and opens no approval. Because
        the served bundle reads the source's current snapshot, re-pinning is exactly what
        changes what serves; the prior snapshot stays replayable. FAIL CLOSED if the commit is
        not in this source's history. Admin CONFIGURE-gated, admin-surface-only, off MCP (no
        served contract / tools_hash moves). Workspace-scoped (hq-t6nx); the re-pin and its
        audit append are COUPLED in ONE transaction -- a failed audit rolls the re-pin back, so
        the serving pin is never moved without an audit row."""
        denial = admin_config_authorization_error(self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        params = self._json_body()
        if params is None:
            return
        source_id = str(params.get("source_id", "")).strip()
        commit_sha = str(params.get("commit_sha", "")).strip()
        if not source_id or not commit_sha:
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    "rollback needs a source_id and commit_sha",
                    recovery="POST { source_id, commit_sha } for a prior commit from the history",
                ),
            )
            return
        workspace = self._principal_workspace()
        session_factory = self.server.session_factory
        actor, issuer = self._audit_actor()
        saved = None
        try:
            # The re-pin write and its audit row are made in THIS session and committed
            # together (mirroring enable/sync): if the audit append fails, the re-pin rolls
            # back too, so the serving snapshot is never moved without an audit record.
            with session_factory() as session, session.begin():
                saved = PostgresContextRepository(session_factory).repin_snapshot(
                    source_id, commit_sha=commit_sha, workspace=workspace, session=session
                )
                PostgresAdminAuditRepository(session_factory).record(
                    actor=actor,
                    actor_issuer=issuer,
                    workspace=workspace,
                    action="context_source.rollback",
                    target=source_id,
                    result="ok",
                    detail=f"rolled back to {commit_sha}",
                    session=session,
                )
        except NotFoundError as exc:
            # Unknown source, cross-workspace source, or a commit not in this source's history
            # (all reported as NotFound by `repin_snapshot`): nothing was changed.
            self._audit(
                action="context_source.rollback",
                target=source_id,
                result="error",
                detail="not found",
            )
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    str(exc),
                    recovery="choose a commit this source has snapshotted (GET the history)",
                ),
            )
            return
        except Exception as exc:  # noqa: BLE001 -- an unaudited mutation must not persist
            print(
                f"WARNING: context_source.rollback rolled back: {type(exc).__name__}",
                file=sys.stderr,
            )
            self._fail(
                500,
                OperationError(
                    INTERNAL_ERROR,
                    "the serving snapshot could not be re-pinned together with its audit record",
                    recovery="retry the request once the datastore is reachable",
                ),
            )
            return
        self._respond(200, {"schema_version": SCHEMA_VERSION, "source": _source_record_view(saved)})

    # --- admin observed-evidence CONNECTION management (hq-jedd) ---

    def _get_connections(self) -> None:
        """List the observed-evidence connections for the admin manage surface (hq-jedd).

        Admin CONFIGURE-gated -- the SAME boundary as the mutations, and consistent with the
        context-source / write-back-target rulings: the list exposes deployment config
        (connector types and config references), so a generic READ principal must not read
        it. It carries NO secret -- only the non-secret `config_ref` (a base URL / bundle
        path, userinfo-redacted) and the recorded health. Credentials live only in the server
        environment and are never on a connection row."""
        denial = admin_config_authorization_error(self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        workspace = self._principal_workspace()
        repository = PostgresConnectionRepository(self.server.session_factory)
        connections = repository.list(workspace=workspace)
        # `has_observed_assets` lets the admin UI pre-disable Remove for a connection that
        # holds governed evidence (removal is refused server-side either way -- disable-only,
        # hq-jedd). A boolean, not a count: the UI only needs the disable-only signal.
        self._respond(
            200,
            {
                "schema_version": SCHEMA_VERSION,
                "connections": [
                    _connection_view(
                        c,
                        has_observed_assets=repository.observed_asset_count(
                            c.id, workspace=workspace
                        )
                        > 0,
                    )
                    for c in connections
                ],
            },
        )

    def _post_connection(self) -> None:
        """Add or update an observed-evidence connection (hq-jedd). ADMIN settings write:
        connector_type + display_name + a non-secret config_ref (a base URL or bundle path).
        No credential is accepted or stored -- the connector reads its secret from the server
        environment at probe/sync time. A credential-bearing URL is refused, exactly like the
        context-source pointer."""
        from hyperset.connectors.build import CONNECTOR_BUILDERS

        denial = admin_config_authorization_error(self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        params = self._json_body()
        if params is None:
            return
        connector_type = str(params.get("connector_type", "")).strip()
        display_name = str(params.get("display_name", "")).strip()
        config_ref = str(params.get("config_ref", "")).strip()
        connection_id = str(params.get("id", "")).strip() or None
        if connector_type not in CONNECTOR_BUILDERS:
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    f"connector_type must be one of {', '.join(sorted(CONNECTOR_BUILDERS))}; "
                    f"not {connector_type!r}",
                    recovery="choose a supported connector type",
                ),
            )
            return
        if not display_name or not config_ref:
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    "a connection needs a display_name and a config_ref (a base URL or a "
                    "bundle path)",
                    recovery="POST { connector_type, display_name, config_ref, id? }",
                ),
            )
            return
        if _pointer_has_credentials(config_ref):
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    "the config_ref must not embed credentials in its URL; a token or password "
                    "there would be persisted and shown in the admin audit trail",
                    recovery="remove the user:token@ from the URL; set the credential in the "
                    "server environment (HYPERSET_SUPERSET_USERNAME/PASSWORD, "
                    "HYPERSET_DATAHUB_TOKEN), never on the connection",
                ),
            )
            return
        actor, issuer = self._audit_actor()
        session_factory = self.server.session_factory
        saved = None
        try:
            with session_factory() as session, session.begin():
                repo = PostgresConnectionRepository(session_factory)
                saved = repo.create_or_update(
                    connection_id=connection_id,
                    connector_type=connector_type,
                    display_name=display_name,
                    config_ref=config_ref,
                    workspace=self._principal_workspace(),
                    session=session,
                )
                PostgresAdminAuditRepository(session_factory).record(
                    actor=actor,
                    actor_issuer=issuer,
                    workspace=self._principal_workspace(),
                    action="connection.set",
                    target=f"{saved.id}:{connector_type}",
                    result="ok",
                    detail=_redact_free_text_userinfo(f"{connector_type}:{config_ref}"),
                    session=session,
                )
        except NotFoundError as exc:
            self._fail(400, OperationError(INVALID_REQUEST, str(exc), recovery="omit id to add"))
            return
        except Exception as exc:  # noqa: BLE001 -- an unaudited mutation must not persist
            print(f"WARNING: connection.set rolled back: {type(exc).__name__}", file=sys.stderr)
            self._fail(
                500,
                OperationError(
                    INTERNAL_ERROR,
                    "the connection could not be saved together with its audit record",
                    recovery="retry the request once the datastore is reachable",
                ),
            )
            return
        self._respond(
            200, {"schema_version": SCHEMA_VERSION, "connection": _connection_view(saved)}
        )

    def _load_manage_connection(self):
        """Admin-gate, read the body, and load the addressed connection for a manage action
        (hq-jedd). Returns (params, repository, record) or None after sending the 400/404, so
        enable/remove/probe share one auth+lookup path."""
        params = self._json_body()
        if params is None:
            return None
        denial = admin_config_authorization_error(self._principal())
        if denial is not None:
            self._fail(400, denial)
            return None
        connection_id = str(params.get("id", "")).strip()
        if not connection_id:
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    "this action needs the connection id",
                    recovery="pass the id of the connection (from the connections list)",
                ),
            )
            return None
        repository = PostgresConnectionRepository(self.server.session_factory)
        try:
            # Workspace-scoped load (hq-t6nx): a connection in another tenant is a 404
            # here, so enable/remove/probe can never reach across workspaces.
            record = repository.get(connection_id, workspace=self._principal_workspace())
        except NotFoundError as exc:
            self._fail(404, OperationError(INVALID_REQUEST, str(exc), recovery="list connections"))
            return None
        return params, repository, record

    def _post_connection_enable(self) -> None:
        """Enable or disable a connection (hq-jedd). A disabled connection keeps its config
        and health history but is excluded from serving (catalog/resolve filter enabled)."""
        loaded = self._load_manage_connection()
        if loaded is None:
            return
        params, _repository, record = loaded
        enabled = bool(params.get("enabled"))
        actor, issuer = self._audit_actor()
        session_factory = self.server.session_factory
        saved = None
        try:
            with session_factory() as session, session.begin():
                saved = PostgresConnectionRepository(session_factory).set_enabled(
                    record.id,
                    enabled=enabled,
                    workspace=self._principal_workspace(),
                    session=session,
                )
                PostgresAdminAuditRepository(session_factory).record(
                    actor=actor,
                    actor_issuer=issuer,
                    workspace=self._principal_workspace(),
                    action="connection.enable" if enabled else "connection.disable",
                    target=f"{record.id}:{record.connector_type}",
                    result="ok",
                    detail=f"enabled={enabled}",
                    session=session,
                )
        except Exception as exc:  # noqa: BLE001 -- an unaudited mutation must not persist
            print(f"WARNING: connection.enable rolled back: {type(exc).__name__}", file=sys.stderr)
            self._fail(
                500,
                OperationError(
                    INTERNAL_ERROR,
                    "the connection state could not be saved together with its audit record",
                    recovery="retry the request once the datastore is reachable",
                ),
            )
            return
        self._respond(
            200, {"schema_version": SCHEMA_VERSION, "connection": _connection_view(saved)}
        )

    def _post_connection_remove(self) -> None:
        """Remove a connection that has NO observed assets (hq-jedd). Observed assets are
        governed evidence keyed on the connection, so a connection WITH history is
        disable-only (this refuses it, pointing at disable); only a mis-added connection with
        no observed assets is deletable."""
        loaded = self._load_manage_connection()
        if loaded is None:
            return
        _params, repository, record = loaded
        if repository.observed_asset_count(record.id, workspace=self._principal_workspace()) > 0:
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    f"connection {record.id} has observed assets, so it cannot be removed "
                    "(observed evidence keeps its source identity)",
                    recovery="disable it instead -- a disabled connection is excluded from "
                    "serving but its evidence and history are preserved",
                ),
            )
            return
        actor, issuer = self._audit_actor()
        session_factory = self.server.session_factory
        try:
            with session_factory() as session, session.begin():
                PostgresConnectionRepository(session_factory).delete(
                    record.id, workspace=self._principal_workspace(), session=session
                )
                PostgresAdminAuditRepository(session_factory).record(
                    actor=actor,
                    actor_issuer=issuer,
                    workspace=self._principal_workspace(),
                    action="connection.remove",
                    target=f"{record.id}:{record.connector_type}",
                    result="ok",
                    detail="removed (no observed assets)",
                    session=session,
                )
        except Exception as exc:  # noqa: BLE001 -- an unaudited mutation must not persist
            print(f"WARNING: connection.remove rolled back: {type(exc).__name__}", file=sys.stderr)
            self._fail(
                500,
                OperationError(
                    INTERNAL_ERROR,
                    "the connection could not be removed together with its audit record",
                    recovery="retry the request once the datastore is reachable",
                ),
            )
            return
        self._respond(200, {"schema_version": SCHEMA_VERSION, "removed": record.id})

    def _post_connection_probe(self) -> None:
        """Bounded LIVE probe of one connection (hq-jedd): build the connector and call its
        test_connection, then report configured/reachable/fresh + reason/impact/recovery.
        GREEN LIVENESS IS NOT READINESS -- a reachable but never/stale-synced source is
        reported DEGRADED, not green. The recorded health + its audit append are coupled in
        one transaction. No secret is returned; the reason is userinfo-redacted."""
        from hyperset.ops.connection_probe import probe_connection

        loaded = self._load_manage_connection()
        if loaded is None:
            return
        _params, repository, record = loaded
        latest = select_latest_finished(
            PostgresSyncRepository(self.server.session_factory).list_runs(record.id)
        )
        # Pass the whole latest FINISHED run (not just its timestamp) so the live probe's
        # freshness uses the SAME `freshness_from_run` rule the recorded overview does: a
        # recent FAILED sync is stale, not fresh (hq-hnrf area 2 round 1).
        probe = probe_connection(record, latest_finished_run=latest)
        reason = _redact_free_text_userinfo(probe.reason)
        actor, issuer = self._audit_actor()
        session_factory = self.server.session_factory
        try:
            with session_factory() as session, session.begin():
                PostgresConnectionRepository(session_factory).record_health(
                    record.id,
                    status=probe.health_status,
                    detail=reason,
                    workspace=self._principal_workspace(),
                    session=session,
                )
                PostgresAdminAuditRepository(session_factory).record(
                    actor=actor,
                    actor_issuer=issuer,
                    workspace=self._principal_workspace(),
                    action="connection.probe",
                    target=f"{record.id}:{record.connector_type}",
                    result=probe.status,
                    detail=f"probe={probe.status}",
                    session=session,
                )
        except Exception as exc:  # noqa: BLE001 -- an unaudited mutation must not persist
            print(f"WARNING: connection.probe rolled back: {type(exc).__name__}", file=sys.stderr)
            self._fail(
                500,
                OperationError(
                    INTERNAL_ERROR,
                    "the probe result could not be saved together with its audit record",
                    recovery="retry the request once the datastore is reachable",
                ),
            )
            return
        self._respond(
            200,
            {
                "schema_version": SCHEMA_VERSION,
                "probe": {
                    "connection_id": record.id,
                    "status": probe.status,
                    "configured": probe.configured,
                    "reachable": probe.reachable,
                    "fresh": probe.fresh,
                    "reason": reason,
                    "impact": probe.impact,
                    "recovery": probe.recovery,
                },
            },
        )

    def _get_writeback_config(self) -> None:
        """The configured proposal-only write-back target, or null (hy-8o8m).
        Read-only: it reports the target the admin set, and confers no
        authority."""
        # An operator READ behind the playground: gated by the same fail-closed
        # decision as every governed read (hy-lrho). Off by default: no gate, no
        # change; enabled with no verified reader, denied.
        denial = authorization_error(None, {}, self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        config = PostgresWritebackConfigRepository(self.server.session_factory).get(
            workspace=self._principal_workspace()
        )
        self._respond(
            200,
            {
                "schema_version": SCHEMA_VERSION,
                "config": None if config is None else _writeback_view(config),
            },
        )

    def _writeback_token_fields(self, params: dict, current) -> dict | None:
        """Build a target's token-source fields from the request, or send a 400 and
        return None. Shared by the default-config setter and the per-target manage
        upsert (hq-095h): 'env_ref' stores the NAME of a server-side secret
        (hy-eji4); 'encrypted' encrypts a pasted PAT server-side and stores only the
        ciphertext (hy-up4k); 'github_app' stores the App id and the App private key
        encrypted server-side (hy-bdhg). No raw secret is ever stored in cleartext or
        returned. A blank secret box on update keeps the stored ciphertext (a
        write-only field, never pre-filled)."""
        token_source = str(params.get("token_source", "") or "env_ref").strip()
        if token_source not in ("env_ref", "encrypted", "github_app"):
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    f"token_source must be 'env_ref', 'encrypted', or 'github_app', "
                    f"not {token_source!r}",
                    recovery=(
                        "choose 'env_ref' (an env var NAME), 'encrypted' (a pasted token), "
                        "or 'github_app' (App id + App private key)"
                    ),
                ),
            )
            return None
        fields: dict = {"token_source": token_source}
        if token_source == "env_ref":
            fields["token_ref"] = str(params.get("token_ref", "")).strip() or None
        elif token_source == "encrypted":
            from hyperset.security.secret_box import SecretBoxError, encrypt

            token = str(params.get("token", ""))
            if token:
                try:
                    ciphertext, nonce = encrypt(token)
                except SecretBoxError as exc:
                    self._fail(
                        400,
                        OperationError(
                            INVALID_REQUEST,
                            f"could not encrypt the token: {exc}",
                            recovery=(
                                "set HYPERSET_SECRET_KEY to base64 of 32 random bytes, or use "
                                "the env-var-name token source instead"
                            ),
                        ),
                    )
                    return None
                fields["token_ciphertext"] = ciphertext
                fields["token_nonce"] = nonce
            elif current is not None and current.mode == "encrypted":
                # Write-only field: re-saving other settings without re-pasting
                # the token keeps the stored ciphertext (never pre-filled).
                fields["token_ciphertext"] = current.token_ciphertext
                fields["token_nonce"] = current.token_nonce
        else:  # github_app
            app_id_raw = str(params.get("app_id", "")).strip()
            if not app_id_raw.isdigit() or int(app_id_raw) <= 0:
                self._fail(
                    400,
                    OperationError(
                        INVALID_REQUEST,
                        "a github_app token source needs a positive integer app_id",
                        recovery="paste the numeric App ID from the GitHub App's settings page",
                    ),
                )
                return None
            fields["app_id"] = int(app_id_raw)
            from hyperset.security.secret_box import SecretBoxError, encrypt

            private_key = str(params.get("app_private_key", ""))
            if private_key:
                try:
                    ciphertext, nonce = encrypt(private_key)
                except SecretBoxError as exc:
                    self._fail(
                        400,
                        OperationError(
                            INVALID_REQUEST,
                            f"could not encrypt the App private key: {exc}",
                            recovery=(
                                "set HYPERSET_SECRET_KEY to base64 of 32 random bytes so the "
                                "App private key can be stored encrypted at rest"
                            ),
                        ),
                    )
                    return None
                fields["app_key_ciphertext"] = ciphertext
                fields["app_key_nonce"] = nonce
            elif current is not None and current.mode == "github_app":
                # Write-only field: re-saving with the key box blank keeps the
                # stored encrypted key (never pre-filled).
                fields["app_key_ciphertext"] = current.app_key_ciphertext
                fields["app_key_nonce"] = current.app_key_nonce
        return fields

    def _get_writeback_targets(self) -> None:
        """List every write-back TARGET for the admin manage surface (hq-095h).

        Gated by the ADMIN CONFIGURE authz, the SAME boundary as the target
        mutations (hq-1aap round 2): the list exposes deployment config -- target
        repositories and secret REFERENCES (env var names, App ids) -- so a
        generic READ principal must not read it. It still returns no secret VALUE
        (only references and a `token_set` flag). A public-prefix caller is already
        refused before this handler; this is the server-side authz, not the
        surface split."""
        denial = admin_config_authorization_error(self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        targets = PostgresWritebackConfigRepository(self.server.session_factory).list(
            workspace=self._principal_workspace()
        )
        self._respond(
            200,
            {
                "schema_version": SCHEMA_VERSION,
                "targets": [_writeback_target_view(t) for t in targets],
            },
        )

    def _post_writeback_target(self) -> None:
        """Add or update a per-domain routing TARGET (hq-095h). ADMIN settings
        write: a keyed target (routing_key = the domain it serves) with its
        repository/base_ref/manifest_path and token source. It approves, merges,
        and proposes nothing (ADR 0012). The default/catch-all target is managed
        by the write-back config path, not here."""
        params = self._json_body()
        if params is None:
            return
        denial = admin_config_authorization_error(self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        routing_key = str(params.get("routing_key", "")).strip()
        if not routing_key:
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    "a routing target needs a routing_key (the domain it serves)",
                    recovery=(
                        "set routing_key to the domain this target should receive proposals "
                        "for; the default/catch-all target is managed in write-back settings"
                    ),
                ),
            )
            return
        values = {
            key: str(params.get(key, "")).strip()
            for key in ("repository", "base_ref", "manifest_path")
        }
        missing = [key for key, value in values.items() if not value]
        if missing:
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    f"a write-back target needs repository, base_ref, and manifest_path; "
                    f"missing {', '.join(missing)}",
                    recovery="set all three; the repository may be a URL or a local path",
                ),
            )
            return
        if _pointer_has_credentials(values["repository"]):
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    "the repository target must not embed credentials in its URL; a token "
                    "or password there would be persisted and shown in the admin audit trail",
                    recovery="remove the user:token@ from the URL and set the token via the "
                    "token source (env_ref/encrypted/github_app), not the repository URL",
                ),
            )
            return
        # The reviewer handle(s) a proposal routed to this target goes to (hq-1rq7),
        # comma-separated and OPTIONAL: empty is the honest FAIL-CLOSED needs-routing
        # state, so a target may exist before its reviewers are assigned. No secret.
        reviewer_routing = str(params.get("reviewer_routing", "")).strip() or None
        session_factory = self.server.session_factory
        repository = PostgresWritebackConfigRepository(session_factory)
        # The raw existing keyed row (list() applies no enabled filter), so a blank
        # secret box on update keeps the stored ciphertext even for a disabled target.
        workspace = self._principal_workspace()
        current = next(
            (t for t in repository.list(workspace=workspace) if t.routing_key == routing_key), None
        )
        fields = self._writeback_token_fields(params, current)
        if fields is None:
            return
        actor, issuer = self._audit_actor()
        try:
            with session_factory() as session, session.begin():
                saved = PostgresWritebackConfigRepository(session_factory).set(
                    routing_key=routing_key,
                    reviewer_routing=reviewer_routing,
                    workspace=workspace,
                    **values,
                    **fields,
                    session=session,
                )
                PostgresAdminAuditRepository(session_factory).record(
                    actor=actor,
                    actor_issuer=issuer,
                    workspace=self._principal_workspace(),
                    action="writeback_target.set",
                    target=f"{routing_key}:{_redact_pointer(saved.repository)}",
                    result="ok",
                    detail=f"token_source={fields['token_source']}",
                    session=session,
                )
        except Exception as exc:  # noqa: BLE001 -- an unaudited mutation must not persist
            print(
                f"WARNING: writeback_target.set rolled back: {type(exc).__name__}",
                file=sys.stderr,
            )
            self._fail(
                500,
                OperationError(
                    INTERNAL_ERROR,
                    "the write-back target could not be saved together with its audit "
                    "record, so nothing was written; retry",
                    recovery="retry the request once the datastore is reachable",
                ),
            )
            return
        self._respond(
            200, {"schema_version": SCHEMA_VERSION, "target": _writeback_target_view(saved)}
        )

    def _load_manage_target(self):
        """Admin-gate, read the body, and load the addressed target for a manage
        action (hq-095h). Returns (params, repository, target_record) or None after
        sending the appropriate 400/404 -- so enable/disable/delete/test share one
        auth+lookup path and cannot diverge."""
        params = self._json_body()
        if params is None:
            return None
        denial = admin_config_authorization_error(self._principal())
        if denial is not None:
            self._fail(400, denial)
            return None
        target_id = str(params.get("id", "")).strip()
        if not target_id:
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    "this action needs the target id",
                    recovery="pass the id of the target (from the targets list)",
                ),
            )
            return None
        repository = PostgresWritebackConfigRepository(self.server.session_factory)
        # Workspace-scoped load (hq-t6nx): a target in another tenant is a 404 here, so
        # enable/delete/test can never reach across workspaces.
        target = repository.get_by_id(target_id, workspace=self._principal_workspace())
        if target is None:
            self._fail(
                404,
                OperationError(
                    INVALID_REQUEST,
                    f"no write-back target with id {target_id!r}",
                    recovery="list the targets and use a current id",
                ),
            )
            return None
        return params, repository, target

    def _post_writeback_target_enable(self) -> None:
        """Enable or disable a target by id (hq-095h). A disabled target keeps its
        config but is excluded from routing (`get_by_routing` filters on enabled),
        so an operator can pause a target a probe found unreachable."""
        loaded = self._load_manage_target()
        if loaded is None:
            return
        params, repository, target = loaded
        enabled = bool(params.get("enabled"))
        actor, issuer = self._audit_actor()
        audit_target = f"{target.routing_key or 'default'}:{_redact_pointer(target.repository)}"
        session_factory = self.server.session_factory
        saved = None
        try:
            with session_factory() as session, session.begin():
                repo = PostgresWritebackConfigRepository(session_factory)
                saved = repo.set_enabled(
                    target.id, enabled, workspace=self._principal_workspace(), session=session
                )
                PostgresAdminAuditRepository(session_factory).record(
                    actor=actor,
                    actor_issuer=issuer,
                    workspace=self._principal_workspace(),
                    action="writeback_target.enable" if enabled else "writeback_target.disable",
                    target=audit_target,
                    result="ok",
                    detail=f"enabled={enabled}",
                    session=session,
                )
        except Exception as exc:  # noqa: BLE001 -- an unaudited mutation must not persist
            print(
                f"WARNING: writeback_target.enable rolled back: {type(exc).__name__}",
                file=sys.stderr,
            )
            self._fail(
                500,
                OperationError(
                    INTERNAL_ERROR,
                    "the target state could not be saved together with its audit record",
                    recovery="retry the request once the datastore is reachable",
                ),
            )
            return
        self._respond(
            200, {"schema_version": SCHEMA_VERSION, "target": _writeback_target_view(saved)}
        )

    def _post_writeback_target_delete(self) -> None:
        """Delete a target by id (hq-095h). The default/catch-all target is NOT
        deletable here -- disable it instead -- so a routing fallback is never
        silently removed."""
        loaded = self._load_manage_target()
        if loaded is None:
            return
        _params, repository, target = loaded
        if target.is_default:
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    "the default write-back target cannot be deleted here",
                    recovery="disable it, or re-point it in write-back settings",
                ),
            )
            return
        actor, issuer = self._audit_actor()
        audit_target = f"{target.routing_key or 'default'}:{_redact_pointer(target.repository)}"
        session_factory = self.server.session_factory
        try:
            with session_factory() as session, session.begin():
                PostgresWritebackConfigRepository(session_factory).delete(
                    target.id, workspace=self._principal_workspace(), session=session
                )
                PostgresAdminAuditRepository(session_factory).record(
                    actor=actor,
                    actor_issuer=issuer,
                    workspace=self._principal_workspace(),
                    action="writeback_target.delete",
                    target=audit_target,
                    result="ok",
                    detail="deleted",
                    session=session,
                )
        except Exception as exc:  # noqa: BLE001 -- an unaudited mutation must not persist
            print(
                f"WARNING: writeback_target.delete rolled back: {type(exc).__name__}",
                file=sys.stderr,
            )
            self._fail(
                500,
                OperationError(
                    INTERNAL_ERROR,
                    "the target could not be deleted together with its audit record",
                    recovery="retry the request once the datastore is reachable",
                ),
            )
            return
        self._respond(200, {"schema_version": SCHEMA_VERSION, "deleted": target.id})

    def _post_writeback_target_test(self) -> None:
        """PROBE a target for reachability WITHOUT opening a branch or PR (hq-095h).

        Resolves the target's server-side credential (fail-closed to `blocked` when
        a URL target's auth ref does not resolve), then runs a read-only
        `git ls-remote` that lists the target's refs but writes nothing. Returns
        ready / degraded / blocked with a reason and recovery. The persisted
        `test_result` is a short status string; NO token value appears in the
        response, the stored result, or a log."""
        loaded = self._load_manage_target()
        if loaded is None:
            return
        _params, _repository, target = loaded
        from hyperset.transport.operations import resolve_writeback_token

        try:
            token = resolve_writeback_token(target)
        except OperationError as exc:
            # The auth reference does not resolve -- fail closed to blocked, and
            # reuse the operation's own credential-free message and recovery.
            if not self._record_probe_result(target, "blocked"):
                return
            self._respond(
                200,
                {
                    "schema_version": SCHEMA_VERSION,
                    "probe": {
                        "target_id": target.id,
                        "status": "blocked",
                        "reason": exc.message,
                        "recovery": exc.recovery,
                    },
                },
            )
            return
        from hyperset.flywheel.git_pr import probe_target

        probe = probe_target(repository=target.repository, base_ref=target.base_ref, token=token)
        if not probe.reachable:
            status = "blocked"
            reason = f"the target could not be reached or authenticated: {probe.detail}"
            recovery = (
                "check the repository URL/path is correct and reachable, and (URL targets) "
                "that the token source resolves to a credential with access"
            )
        elif not probe.base_ref_exists:
            status = "degraded"
            reason = (
                f"the repository is reachable and authenticated, but base_ref "
                f"{target.base_ref!r} was not found"
            )
            recovery = "create the base branch, or point base_ref at an existing branch"
        else:
            status = "ready"
            reason = f"reachable, authenticated, and base_ref {target.base_ref!r} exists"
            recovery = ""
        if not self._record_probe_result(target, status):
            return
        self._respond(
            200,
            {
                "schema_version": SCHEMA_VERSION,
                "probe": {
                    "target_id": target.id,
                    "status": status,
                    "reason": reason,
                    "recovery": recovery,
                },
            },
        )

    def _record_probe_result(self, target, status: str) -> bool:
        """Persist a probe's `test_result` AND its admin audit append in ONE
        transaction (hq-095h round 2). Persisting the probe outcome is an admin
        mutation, so it must not land without an audit row: if either the
        `test_result` write or the audit append fails, both roll back and the
        handler answers 500. Returns True on success; on failure it has already
        sent the 500, so the caller just returns. The status is a short
        non-secret string (ready/degraded/blocked); no token value is written."""
        actor, issuer = self._audit_actor()
        audit_target = f"{target.routing_key or 'default'}:{_redact_pointer(target.repository)}"
        session_factory = self.server.session_factory
        try:
            with session_factory() as session, session.begin():
                PostgresWritebackConfigRepository(session_factory).record_test_result(
                    target.id, status, workspace=self._principal_workspace(), session=session
                )
                PostgresAdminAuditRepository(session_factory).record(
                    actor=actor,
                    actor_issuer=issuer,
                    workspace=self._principal_workspace(),
                    action="writeback_target.test",
                    target=audit_target,
                    result=status,
                    detail=f"probe={status}",
                    session=session,
                )
        except Exception as exc:  # noqa: BLE001 -- an unaudited mutation must not persist
            print(
                f"WARNING: writeback_target.test rolled back: {type(exc).__name__}",
                file=sys.stderr,
            )
            self._fail(
                500,
                OperationError(
                    INTERNAL_ERROR,
                    "the probe result could not be saved together with its audit record, "
                    "so nothing was written; retry",
                    recovery="retry the request once the datastore is reachable",
                ),
            )
            return False
        return True

    def _post_writeback_config(self) -> None:
        """Set the write-back TARGET (hy-8o8m). The admin panel's first write
        path, and it writes ONLY the target: it approves, merges, and proposes
        nothing (ADR 0012)."""
        params = self._json_body()
        if params is None:
            return
        # ADMIN authorization on the write path (hy-2nqb). Setting the write-back target
        # is a deployment-config write, so it requires an `admin` (`configure`) grant,
        # server-side, when the authz gate is enabled -- BEFORE this deployment is
        # exposed non-loopback. With the gate OFF (the default) this is a no-op and the
        # write stays unauthenticated, which is the LOCAL-ONLY dev shortcut (the operator
        # drives it on loopback); enabling authz is what closes it, denying an
        # unauthenticated caller or an insufficient role. The body is read first only so
        # the socket is drained; nothing is acted on before this check.
        denial = admin_config_authorization_error(self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        values = {
            key: str(params.get(key, "")).strip()
            for key in ("repository", "base_ref", "manifest_path")
        }
        missing = [key for key, value in values.items() if not value]
        if missing:
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    f"a write-back target needs repository, base_ref, and manifest_path; "
                    f"missing {', '.join(missing)}",
                    recovery="set all three; the repository may be a URL or a local path",
                ),
            )
            return
        if _pointer_has_credentials(values["repository"]):
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    "the repository target must not embed credentials in its URL; a token "
                    "or password there would be persisted and shown in the admin audit trail",
                    recovery="remove the user:token@ from the URL and set the token via the "
                    "token source (env_ref/encrypted/github_app), not the repository URL",
                ),
            )
            return
        # The token-source fields (a NAME in env_ref, ciphertext in encrypted, an
        # App id + encrypted key in github_app) are built by the shared helper, so
        # the per-target manage surface (hq-095h) stores a secret exactly the same
        # way. No raw secret is ever stored in cleartext or returned to the browser.
        # The reviewer handle(s) a proposal that falls back to the DEFAULT target
        # goes to (hq-hig7, #435 round 3): the common case a keyed target does not
        # cover. Comma-separated, OPTIONAL; empty is the honest FAIL-CLOSED
        # needs-routing state. No secret. Editing it here PRESERVES the default's
        # disable-only + no-delete + identity guards -- this path only upserts the
        # single default row's config, it never deletes or re-identifies it.
        reviewer_routing = str(params.get("reviewer_routing", "")).strip() or None
        workspace = self._principal_workspace()
        repository = PostgresWritebackConfigRepository(self.server.session_factory)
        current = repository.get(workspace=workspace)
        fields = self._writeback_token_fields(params, current)
        if fields is None:
            return
        # Couple the config write and its audit append in ONE transaction: if either fails,
        # both roll back, so the target is never saved without an audit row (hy-gh-75 round 2).
        # The audited target is the repository with any URL userinfo redacted, and the token
        # SOURCE (a mode, e.g. env_ref/encrypted/github_app), never the secret value.
        actor, issuer = self._audit_actor()
        session_factory = self.server.session_factory
        try:
            with session_factory() as session, session.begin():
                saved = PostgresWritebackConfigRepository(session_factory).set(
                    reviewer_routing=reviewer_routing,
                    workspace=workspace,
                    **values,
                    **fields,
                    session=session,
                )
                PostgresAdminAuditRepository(session_factory).record(
                    actor=actor,
                    actor_issuer=issuer,
                    workspace=self._principal_workspace(),
                    action="writeback_config.set",
                    target=_redact_pointer(saved.repository),
                    result="ok",
                    detail=f"token_source={fields['token_source']}",
                    session=session,
                )
        except Exception as exc:  # noqa: BLE001 -- an unaudited mutation must not persist
            print(
                f"WARNING: writeback_config.set rolled back: {type(exc).__name__}",
                file=sys.stderr,
            )
            self._fail(
                500,
                OperationError(
                    INTERNAL_ERROR,
                    "the write-back target could not be saved together with its audit "
                    "record, so nothing was written; retry",
                    recovery="retry the request once the datastore is reachable",
                ),
            )
            return
        self._respond(200, {"schema_version": SCHEMA_VERSION, "config": _writeback_view(saved)})

    def _post_review_request_evidence(self) -> None:
        """Re-gather a review task's observed evidence (hy-to8m). A review-AUTHORING mutation
        (it replaces the task's assist `gathered_sources` by re-running the deterministic
        step-2 gather), so it carries the SAME REVIEW gate the review-authoring ops enforce --
        decided FIRST, so a denied caller learns nothing. It does not route through
        `run_operation` (the op is off OPERATIONS/MCP by design), so it names the gate and the
        impl explicitly. NOT SERVING: it writes only the UNAPPROVED payload, advances no
        status, and runs no governed write or SQL (ADR 0012)."""
        denial = authorization_error(REQUEST_REVIEW_EVIDENCE, {}, self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        params = self._json_body()
        if params is None:
            return
        try:
            result = _request_review_evidence(
                params,
                session_factory=self.server.session_factory,
                workspace=_principal_workspace(self._principal()),
            )
        except OperationError as exc:
            self._fail(500 if exc.code == INTERNAL_ERROR else 400, exc)
            return
        self._respond(200, result)

    def _post_review_citation_decide(self) -> None:
        """Record a human include/exclude/approve/reject on a citation (hy-cpkvu). A
        review-surface mutation, so it carries the SAME REVIEW gate the review-authoring ops
        enforce -- decided FIRST at the handler so a denied caller learns nothing, and AGAIN
        at the service so a direct call is fail-closed too. It does not route through
        `run_operation` (off OPERATIONS/MCP by design), so it names the gate and impl
        explicitly. NOT SERVING: it writes only the internal audit store, advances no status,
        and runs no governed write or SQL (ADR 0012)."""
        denial = authorization_error(DECIDE_CITATION, {}, self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        params = self._json_body()
        if params is None:
            return
        try:
            result = _decide_citation(
                params,
                session_factory=self.server.session_factory,
                principal=self._principal(),
                workspace=self._principal_workspace(),
            )
        except OperationError as exc:
            self._fail(500 if exc.code == INTERNAL_ERROR else 400, exc)
            return
        self._respond(200, result)

    def _post_review_propose_from_search(self) -> None:
        """Open a proposal-only write-back review task from search hits + a proposed change
        (hy-27nl6, capstone). A review-surface authoring mutation, so it carries the SAME
        REVIEW gate -- decided FIRST at the handler so a denied caller learns nothing, and
        AGAIN at the service so a direct call is fail-closed too. It does not route through
        `run_operation` (off OPERATIONS/MCP by design), so it names the gate and impl
        explicitly. NOT SERVING: it creates a ReviewTask (status open) and approves, writes a
        governed version, opens a PR, and merges NOTHING (ADR 0012)."""
        denial = authorization_error(PROPOSE_CONTEXT_FROM_SEARCH, {}, self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        params = self._json_body()
        if params is None:
            return
        try:
            result = _propose_context_from_search(
                params,
                session_factory=self.server.session_factory,
                principal=self._principal(),
                workspace=self._principal_workspace(),
            )
        except OperationError as exc:
            self._fail(500 if exc.code == INTERNAL_ERROR else 400, exc)
            return
        self._respond(200, result)

    def _get_playground_status(self) -> None:
        """Return non-secret connector and sync metadata for the local UI.

        This is an operator view, not a source probe: connector credentials
        are intentionally not persisted in the response, and a successful
        sync is reported separately from the connection health row.
        """
        # An operator READ behind the playground: gated by the same fail-closed
        # decision as every governed read (hy-lrho). Off by default: no change.
        denial = authorization_error(None, {}, self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        try:
            from playground.ui.app import _playground_runtime_config

            connections = PostgresConnectionRepository(self.server.session_factory).list(
                workspace=self._principal_workspace()
            )
            syncs = PostgresSyncRepository(self.server.session_factory)
            connector_status = []
            for connection in connections:
                # The same deterministic choice the CLI operator view makes, so
                # the two cannot disagree on a tie (hy-9vji #404): a stable
                # tertiary (run id) only changes a double-tie -- equal finished_at
                # AND started_at -- and leaves every distinguishable case as it was.
                latest = select_latest_finished(syncs.list_runs(connection.id))
                checkpoint = syncs.get_checkpoint(connection.id) or {}
                connector_status.append(
                    {
                        "id": connection.id,
                        "connector_type": connection.connector_type,
                        "display_name": connection.display_name,
                        "enabled": connection.enabled,
                        "health_status": connection.health_status,
                        "health_checked_at": _iso(connection.health_checked_at),
                        "health_detail": connection.health_detail,
                        "source_version": checkpoint.get("source_version"),
                        "last_sync": (
                            {
                                "id": latest.id,
                                "status": latest.status,
                                "transport": latest.transport,
                                "started_at": _iso(latest.started_at),
                                "finished_at": _iso(latest.finished_at),
                                "counters": latest.counters,
                                "warnings": latest.warnings,
                                "errors": latest.errors,
                            }
                            if latest
                            else None
                        ),
                    }
                )
            self._respond(
                200,
                {
                    "schema_version": SCHEMA_VERSION,
                    "hyperset": {
                        "status": "ok",
                        "version": __version__,
                        "operations": list(OPERATIONS),
                    },
                    "auth": {
                        "enabled": authz_enabled(),
                        "mode": "oidc" if authz_enabled() else "loopback",
                    },
                    "playground": _playground_runtime_config(),
                    "connectors": connector_status,
                },
            )
        except Exception as exc:  # noqa: BLE001 -- operator route fails closed
            traceback.print_exc()
            self._fail(
                500,
                OperationError(
                    INTERNAL_ERROR,
                    f"the server could not read playground status ({type(exc).__name__})",
                    recovery="retry; if it persists, check the api and postgres logs",
                ),
            )

    def _get_public_docs(self) -> None:
        """Serve the local getting-started guide to the in-app Docs surface."""
        docs_path = Path(__file__).resolve().parents[2] / "docs" / "getting-started.md"
        if not docs_path.is_file():
            self._fail(404, _route_error(PLAYGROUND_DOCS_PATH))
            return
        self._respond(
            200,
            {
                "path": "docs/getting-started.md",
                "markdown": docs_path.read_text(encoding="utf-8"),
            },
        )

    def do_POST(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's contract
        self._begin_request()
        path = self._path()
        if (
            any(path == f"{api_path}/chat" for api_path in PLAYGROUND_API_PATHS)
            and _playground_enabled()
        ):
            self._post_playground_stream()
            return
        if (
            any(
                path
                in {
                    f"{api_path}/demo/query",
                    f"{api_path}/demo/ask",
                }
                for api_path in PLAYGROUND_API_PATHS
            )
            and _playground_enabled()
        ):
            self._post_playground(path)
            return
        via_public_api = False
        for api_path in PLAYGROUND_API_PATHS:
            if path.startswith(f"{api_path}/v0/") and _playground_enabled():
                via_public_api = api_path == PLAYGROUND_USER_API_PATH
                path = path.removeprefix(api_path)
                break
        if path == WRITEBACK_CONFIG_PATH and _playground_enabled():
            # Setting the write-back TARGET is a SETTINGS write, not part of the
            # public reviewer workflow (hy-529x): a public user must not re-point
            # the target repo. So it is refused on the PUBLIC surface (the
            # `/playground/api` prefix) and served only via the admin surface or
            # a direct backend call. This is the "not on the public surface"
            # minimum the Overseer ruled -- a SURFACE gate. The WRITE itself is now
            # additionally ADMIN-authorized server-side when the authz gate is on
            # (hy-2nqb, in `_post_writeback_config`): the surface split alone is not
            # authentication, so a hosted deployment enables authz and only an admin
            # may write. With the gate off (loopback dev) the write stays
            # unauthenticated -- the local-only shortcut.
            if via_public_api:
                self._fail(404, _route_error(self._path()))
                return
            self._post_writeback_config()
            return
        if path == WRITEBACK_TARGETS_PATH and _playground_enabled():
            # Add/update a per-domain routing target -- an admin SETTINGS write, so
            # refused on the public surface (like the write-back config write) and
            # admin-authorized server-side in the handler.
            if via_public_api:
                self._fail(404, _route_error(self._path()))
                return
            self._post_writeback_target()
            return
        if path == WRITEBACK_TARGET_ENABLE_PATH and _playground_enabled():
            if via_public_api:
                self._fail(404, _route_error(self._path()))
                return
            self._post_writeback_target_enable()
            return
        if path == WRITEBACK_TARGET_DELETE_PATH and _playground_enabled():
            if via_public_api:
                self._fail(404, _route_error(self._path()))
                return
            self._post_writeback_target_delete()
            return
        if path == WRITEBACK_TARGET_TEST_PATH and _playground_enabled():
            if via_public_api:
                self._fail(404, _route_error(self._path()))
                return
            self._post_writeback_target_test()
            return
        if path == CONNECTIONS_PATH and _playground_enabled():
            if via_public_api:
                self._fail(404, _route_error(self._path()))
                return
            self._post_connection()
            return
        if path == CONNECTION_ENABLE_PATH and _playground_enabled():
            if via_public_api:
                self._fail(404, _route_error(self._path()))
                return
            self._post_connection_enable()
            return
        if path == CONNECTION_REMOVE_PATH and _playground_enabled():
            if via_public_api:
                self._fail(404, _route_error(self._path()))
                return
            self._post_connection_remove()
            return
        if path == CONNECTION_PROBE_PATH and _playground_enabled():
            if via_public_api:
                self._fail(404, _route_error(self._path()))
                return
            self._post_connection_probe()
            return
        if path == CONTEXT_SOURCES_PATH and _playground_enabled():
            # Adding a context SOURCE is an admin config write (a repo/ref/path pointer, ADR
            # 0012); refused on the public surface, then authz-gated in the handler.
            if via_public_api:
                self._fail(404, _route_error(self._path()))
                return
            self._post_context_source_add()
            return
        if path == CONTEXT_SOURCE_SYNC_PATH and _playground_enabled():
            if via_public_api:
                self._fail(404, _route_error(self._path()))
                return
            self._post_context_source_sync()
            return
        if path == REVIEW_TASK_RECONCILE_PATH and _playground_enabled():
            if via_public_api:
                self._fail(404, _route_error(self._path()))
                return
            self._post_review_task_reconcile()
            return
        if path == REVIEW_TASK_RECONCILE_SWEEP_PATH and _playground_enabled():
            if via_public_api:
                self._fail(404, _route_error(self._path()))
                return
            self._post_review_task_reconcile_sweep()
            return
        if path == CONTEXT_SOURCE_VALIDATE_PATH and _playground_enabled():
            if via_public_api:
                self._fail(404, _route_error(self._path()))
                return
            self._post_context_source_validate()
            return
        if path == CONTEXT_SOURCE_ENABLE_PATH and _playground_enabled():
            if via_public_api:
                self._fail(404, _route_error(self._path()))
                return
            self._post_context_source_enable()
            return
        if path == CONTEXT_SOURCE_REMOVE_PATH and _playground_enabled():
            if via_public_api:
                self._fail(404, _route_error(self._path()))
                return
            self._post_context_source_remove()
            return
        if path == CONTEXT_SOURCE_COMPARE_PATH and _playground_enabled():
            if via_public_api:
                self._fail(404, _route_error(self._path()))
                return
            self._post_context_source_compare()
            return
        if path == CONTEXT_SOURCE_ROLLBACK_PATH and _playground_enabled():
            if via_public_api:
                self._fail(404, _route_error(self._path()))
                return
            self._post_context_source_rollback()
            return
        if path == REVIEW_REQUEST_EVIDENCE_PATH and _playground_enabled():
            self._post_review_request_evidence()
            return
        if path == REVIEW_CITATION_DECIDE_PATH and _playground_enabled():
            self._post_review_citation_decide()
            return
        if path == REVIEW_PROPOSE_FROM_SEARCH_PATH and _playground_enabled():
            self._post_review_propose_from_search()
            return
        operation = ROUTES.get(path)
        if operation is None:
            self._fail(404, _route_error(self._path()))
            return
        body = self._read_body()
        if body is None:
            return
        try:
            params = json.loads(body) if body.strip() else {}
        # BOTH, because they are siblings rather than parent and child (hy-z9o).
        # `UnicodeDecodeError` and `json.JSONDecodeError` both descend from
        # `ValueError` and neither descends from the other, so catching only
        # the second let a body that is not valid UTF-8 escape `do_POST`
        # entirely: socketserver printed a traceback, tore the connection down,
        # and the client got no status line and no body -- the last hole in
        # hy-r1x's "every failure leaves as an OperationError", which
        # `run_operation`'s broad conversion cannot close because the decode
        # happens upstream of it.
        #
        # Named rather than caught as `ValueError`, which would also swallow a
        # `ValueError` raised by anything else this line ever grows to call and
        # report it to the caller as their malformed JSON.
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._fail(400, _decode_error(operation, exc))
            return
        try:
            result = run_operation(
                operation,
                params,
                session_factory=self.server.session_factory,
                principal=self._principal(),
            )
        except OperationError as exc:
            # A request the server could not answer is the server's fault and
            # says so; everything else is the request's. Both keep the
            # connection, because `_read_body` consumed it: what is unhealthy
            # on an `internal_error` is the database session, not the socket,
            # and hanging up would contradict the recovery text the response
            # itself returns -- it says retry.
            self._fail(500 if exc.code == INTERNAL_ERROR else 400, exc)
            return
        self._respond(200, result)

    def _post_playground(self, path: str) -> None:
        body = self._read_body()
        if body is None:
            return
        # The demo endpoints execute SQL or an agent harness, so a network deployment
        # must apply the playground-wide governed-read gate before either is dispatched.
        # Read the body first to keep a persistent HTTP connection synchronized, but do
        # not parse or execute it for an unauthorized caller.
        denial = authorization_error(None, {}, self._principal())
        if denial is not None:
            self._fail(400, denial)
            return
        try:
            params = json.loads(body) if body.strip() else {}
            if not isinstance(params, dict):
                raise ValueError("request body must be a JSON object")
            from playground.ui.app import _run_agent_harness, _run_demo_sql

            result = (
                _run_demo_sql(params.get("sql"))
                if path.endswith("/query")
                else _run_agent_harness(params)
            )
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            self._respond(400, {"error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001 -- the playground exposes provider/tool failures
            traceback.print_exc()
            self._respond(502, {"error": str(exc)})
            return
        self._respond(200, result)

    def _post_playground_stream(self) -> None:
        body = self._read_body()
        if body is None:
            return
        try:
            params = json.loads(body) if body.strip() else {}
            if not isinstance(params, dict):
                raise ValueError("request body must be a JSON object")
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            self._respond(400, {"error": str(exc)})
            return

        # Chat is the same executable playground surface as demo/query: an authenticated
        # network deployment must not allow an unauthenticated caller to invoke the agent
        # harness merely because the request uses the streaming endpoint.
        denial = authorization_error(None, {}, self._principal())
        if denial is not None:
            self._fail(400, denial)
            return

        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def emit(message: dict) -> None:
            payload = json.dumps(message, separators=(",", ":"), default=str)
            self.wfile.write(f"data: {payload}\n\n".encode())
            self.wfile.flush()

        try:
            from playground.ui.app import _stream_playground_chat

            _stream_playground_chat(params, emit)
        except Exception as exc:  # noqa: BLE001 -- stream the failure to the UI
            traceback.print_exc()
            try:
                emit({"type": "error", "error": str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                return

    def handle_one_request(self) -> None:
        # One handler instance serves every request on the connection, so
        # whether a body was consumed has to be reset per request rather than
        # inherited from the last one.
        self._body_consumed = False
        super().handle_one_request()

    def _path(self) -> str:
        return self.path.split("?", 1)[0].rstrip("/") or "/"

    def _read_body(self) -> bytes | None:
        # This check must stay ahead of the `Content-Length` branch. A request
        # carrying both headers would otherwise be read as a `Content-Length`
        # body of a chunked one: the read is short of nothing, so
        # `_body_consumed` goes True, the connection is kept, and the leftover
        # chunk framing is parsed as the next request. The ordering is
        # load-bearing, not stylistic.
        if self.headers.get("Transfer-Encoding"):
            # Nothing here speaks chunked, and a chunked body is framed by the
            # body itself rather than by a header, so it can neither be read
            # nor drained by this handler. Refusing it is the same rule as an
            # unparseable `Content-Length`: the framing is not something the
            # server knows, so the socket cannot be trusted afterwards.
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    (f"{self.headers['Transfer-Encoding']!r} transfer encoding is not supported"),
                    recovery="send the request body whole with a Content-Length header",
                ),
            )
            return None
        # The SAME framing decision the clean-socket check uses, so the length a
        # body is read to and the length the connection is judged clean by can
        # never disagree (that disagreement is itself a desync). `None` means an
        # untrusted framing -- a conflicting duplicate `Content-Length` or a
        # non-`1*DIGIT` value -- which `_fail` closes on, since the leftover bytes
        # would otherwise be read as the next request (hy-670).
        length = self._declared_content_length()
        if length is None:
            self._fail(
                400,
                OperationError(
                    INVALID_REQUEST,
                    "Content-Length must be a single value of digits only",
                    recovery=(
                        "send exactly one Content-Length header whose value is the "
                        "number of body bytes, with no sign, spaces, or duplicates"
                    ),
                ),
            )
            return None
        if length > MAX_BODY_BYTES:
            self._fail(
                413,
                OperationError(
                    REQUEST_TOO_LARGE,
                    f"request body is {length} bytes, over the {MAX_BODY_BYTES} byte limit",
                    recovery="send only the question and the plan, not the data itself",
                ),
            )
            return None
        # A TOTAL BUDGET, not just a silence bound (hy-6zsk). The class timeout
        # reaches the socket and applies per blocking operation, so every byte
        # that arrives restarts it: a client dribbling one byte every 29 seconds
        # never trips it, and `MAX_BODY_BYTES` bounds only the volume. Measured
        # against that ceiling, one connection could hold one handler thread for
        # 352 days before the byte limit is reached, and nothing caps how many
        # such threads exist.
        #
        # So the deadline is captured once, before the first read, and checked
        # between reads. `read1` rather than `read` is what makes that possible:
        # `read(n)` returns only on n bytes or EOF, so the check would not run
        # until the read it is meant to interrupt had already finished.
        deadline = time.monotonic() + self.timeout
        chunks: list[bytes] = []
        received = 0
        try:
            while received < length:
                chunk = self.rfile.read1(min(length - received, READ_CHUNK_BYTES))
                if not chunk:
                    # EOF. A short read, which the line below reports honestly
                    # and `_fail` closes on -- the case that always worked.
                    break
                chunks.append(chunk)
                received += len(chunk)
                if received < length and time.monotonic() >= deadline:
                    self._fail(
                        408,
                        OperationError(
                            INVALID_REQUEST,
                            f"request body took longer than {self.timeout}s to arrive; "
                            f"{received} of {length} declared bytes were received",
                            recovery=(
                                "send the body without pausing, or send a smaller one; this "
                                "server reads a question and a plan, not a data set"
                            ),
                        ),
                    )
                    return None
            body = b"".join(chunks)
        except TimeoutError:
            # ANSWERED, not merely reclaimed. The class timeout alone already
            # frees the thread -- the read raises, the handler unwinds, and
            # socketserver closes the connection -- but the client is left with
            # a torn-down socket and no status line, which is the shape hy-z9o
            # exists to keep out of this server. `_body_consumed` stays False,
            # so `_fail` closes the connection: the declared bytes never
            # arrived and anything still in flight would be read as the next
            # request.
            self._fail(
                408,
                OperationError(
                    INVALID_REQUEST,
                    # SILENCE, which is a different failure from the deadline
                    # above and is worth telling apart: this client stopped
                    # sending, that one kept sending too slowly. The remedies
                    # differ, so the sentences do.
                    f"no request body arrived for {self.timeout}s; "
                    f"{received} of {length} declared bytes were received",
                    recovery=(
                        "send the whole body you declared, or set Content-Length to the "
                        "number of bytes actually sent"
                    ),
                ),
            )
            return None
        # The one place that can honestly claim the socket is clean, and it
        # claims it only for what it actually got: a short read means the rest
        # of the declared body is still coming.
        self._body_consumed = len(body) == length
        return body

    def _declared_content_length(self) -> int | None:
        """This request's declared body length, or `None` when the framing is not
        one this handler can trust and the socket must therefore be treated as
        dirty.

        `None` on three grounds, each a real desync vector this rejects:

        * MULTIPLE `Content-Length` headers that are not byte-identical. Request
          smuggling declares `Content-Length: 0` and `Content-Length: 12`; a
          reader that trusts the first sees no body while 12 bytes wait in the
          socket for the next request. `headers.get()` returns only the FIRST,
          so `get_all()` is load-bearing -- the whole point is to SEE the
          duplicate. Identical duplicates are accepted as the single value.
        * a value outside strict `1*DIGIT`. `int()` accepts `'+0'`, `'-0'`,
          `' 12 '`, `'1_2'`; a `'-0'` read as 0 on a request carrying bytes is
          the signed-zero desync. Only anchored ASCII digits (after stripping
          the optional OWS the field grammar allows) are a length.

        A missing `Content-Length` is `0` -- no body declared. hy-670.
        """
        values = self.headers.get_all("Content-Length")
        if not values:
            return 0
        # OWS is SP and HTAB ONLY (RFC 7230 3.2.3). `str.strip()` with no argument
        # also removes vertical tab and form feed, so `'\x0b0'` would strip to a
        # clean `'0'` and read a body-carrying request as empty on both paths; strip
        # only the two OWS bytes, then require strict digits.
        cleaned = [value.strip(" \t") for value in values]
        if not all(_CONTENT_LENGTH_RE.fullmatch(value) for value in cleaned):
            return None
        distinct = {int(value) for value in cleaned}
        if len(distinct) != 1:
            return None
        return distinct.pop()

    def _socket_is_clean(self) -> bool:
        """Whether this connection can be safely reused: True iff no unread request
        bytes remain in the socket.

        Cleanliness is not "was the body consumed" but "are there unread bytes",
        which has THREE states, not two (hy-670):

            declared 0, read 0   -> clean   (a bodyless GET, and /v0/health)
            declared N, read < N -> dirty   (a GET carrying a body; a short read)
            framing unknown       -> dirty   (chunked, unparseable Content-Length)

        `_body_consumed` collapses the first two into the same `False`, which is
        exactly why moving the `_fail` check into `_respond` would close the
        healthcheck's clean socket every probe. So the declared length is read
        here, not just the consumption flag: a full read is clean, and otherwise
        the socket is clean only when nothing was declared and the framing is one
        this handler understands. Consulted by both `_fail` and `do_GET` so the
        two paths share one framing rule instead of growing divergent ones.
        """
        if self._body_consumed:
            return True
        if self.headers.get("Transfer-Encoding"):
            # Chunked framing is carried by the body, not a header this handler
            # can trust; the same unknown-framing rule as an unparseable length.
            return False
        # Clean only if the request declared no body on framing this handler
        # trusts. `_declared_content_length` returns `None` on an untrusted
        # framing (a conflicting duplicate or a non-`1*DIGIT` value), and
        # `None == 0` is `False`, so an untrusted framing is dirty -- never read
        # as a clean zero (hy-670, the duplicate/signed-zero desync).
        return self._declared_content_length() == 0

    def _fail(self, status: int, error: OperationError, *, allow: str | None = None) -> None:
        """Answer a failure, closing the connection unless this request's socket
        is provably clean -- its body was read to the length it declared, or it
        declared no body at all on framing this handler understands.

        Several failures answer without consuming the request body: an unknown
        route, an oversized one, one whose framing the server does not know --
        an unparseable `Content-Length`, or a `Transfer-Encoding` this handler
        cannot read. On a keep-alive connection those unread bytes are parsed
        as the start of the next request, and the client's next call reads the
        previous answer -- a wrong answer, not an error.

        Cleanliness is read from `_socket_is_clean`, which answers from the
        declared framing and `_body_consumed` (set by `_read_body` only after a
        full read) rather than a fact passed in by the caller. Keying on the
        weaker "did `_read_body` return without failing" fact is what let a
        chunked body through this rule (hy-3ko); the shared predicate keys on the
        declared length instead. Routine refusals do keep the connection -- an
        unknown parameter, a missing directive, a stale bundle, an over-cap limit
        are designed behaviour on a clean socket, and making an agent pay a TCP
        handshake per refusal would be its own regression.

        `do_GET` consults the same predicate before it answers (hy-670): a GET
        reads no body on any path, so one carrying a declared body would answer
        200 and keep a dirty connection unless the success path closed it too.
        Sharing `_socket_is_clean` keeps the failure and success paths on one
        framing rule rather than two that can drift.
        """
        if not self._socket_is_clean():
            # Only ever set: a client that asked to close, or spoke HTTP/1.0,
            # already decided this and must not be overruled here.
            self.close_connection = True
        self._respond(status, error.to_dict(), allow=allow)

    def _begin_request(self) -> None:
        """Start ONE request: mint a fresh correlation id and set it on the per-request
        contextvar (hy-w9ntg). Called at the top of every do_GET/do_POST -- not once per
        connection -- so a keep-alive socket serving a second request gets its OWN id and never
        inherits the first's; the audit repository stamps this id on every row the request
        writes, and it is echoed as `X-Correlation-Id`."""
        # Preserve a caller-provided opaque correlation id so the response header and
        # durable trace use one chain identifier. Invalid values are ignored and replaced
        # with a fresh server id rather than reflected to the caller.
        self._correlation_id = (
            opaque_token(self.headers.get("X-Correlation-Id")) or new_correlation_id()
        )
        set_correlation_id(self._correlation_id)
        # Bind this request's MCP interaction-trace linkage (hy-oqevj) from its headers,
        # fresh per request like the correlation id -- a keep-alive socket must not bleed
        # one caller's session/turn ids into the next. Off-request (a test, a background
        # write) the context stays empty. `self.headers.get` is case-insensitive.
        set_trace_context(trace_context_from_headers(self.headers.get))

    def _respond(self, status: int, payload: dict, *, allow: str | None = None) -> None:
        # Load-bearing, not incidental: the whole body is built before the
        # status line, so a failure while serializing cannot leave a response
        # half-written on a connection this handler then keeps. Streaming a
        # response later would take that guarantee away.
        body = serialize(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        # Echo the request's correlation id so an operator can tie this response to the audit
        # rows it produced (hy-w9ntg). Present on every JSON response; absent only if a
        # response is emitted before _begin_request ran.
        if getattr(self, "_correlation_id", None):
            self.send_header("X-Correlation-Id", self._correlation_id)
        self.send_header("Content-Length", str(len(body)))
        if allow is not None:
            self.send_header("Allow", allow)
        if self.close_connection:
            # Say it on the wire too: the client has to stop reusing the
            # socket, and it cannot see the handler's flag.
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str, *, status: int = 303, cookies: tuple[str, ...] = ()) -> None:
        """A redirect with optional `Set-Cookie` headers and no body -- the login routes'
        only response shape. 303 (default) makes the browser follow with GET; 302 is used
        for the outbound hop to the IdP. `no-store` so a login redirect is never cached."""
        self.send_response(status)
        self.send_header("Location", location)
        for cookie in cookies:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()

    def _cookie(self, name: str) -> str | None:
        """One cookie value from the request `Cookie` header, or `None`. Fail-soft: a
        malformed header is simply no cookie (never an exception that 500s a request)."""
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            jar: SimpleCookie = SimpleCookie()
            jar.load(raw)
        except CookieError:
            return None
        morsel = jar.get(name)
        return morsel.value if morsel is not None else None

    def _cookie_secure(self) -> bool:
        """Whether a Set-Cookie should be `Secure`: true iff the request reached us over
        https. A gateway terminates TLS and forwards `X-Forwarded-Proto: https`; on a plain
        loopback dev run over http the header is absent, so the cookie is not Secure (a
        Secure cookie would be dropped by the browser over http and break the dev flow)."""
        return self.headers.get("X-Forwarded-Proto", "").strip().lower() == "https"

    def _respond_html(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # The HTML references content-hashed assets; never let a browser serve a
        # stale index that points at an old bundle (hy: caused "same bug after fix").
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        if self.close_connection:
            # `do_GET` set this when the request carried an undrained body; tell
            # the client so it stops reusing the socket (hy-670).
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _respond_file(self, path: Path) -> None:
        body = path.read_bytes()
        content_type = {
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".json": "application/json; charset=utf-8",
        }.get(path.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "public, max-age=300")
        self.send_header("Content-Length", str(len(body)))
        if self.close_connection:
            # `do_GET` set this when the request carried an undrained body; tell
            # the client so it stops reusing the socket (hy-670).
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)


def _writeback_view(config) -> dict:
    """The write-back config a client may see: the target, the token SOURCE, and
    whether auth is set -- NEVER any secret value or its ciphertext (hy-up4k,
    hy-bdhg). `token_ref` is the env var NAME in env_ref mode and `app_id` is the
    GitHub App id in github_app mode; both are references, not secrets. The
    encrypted PAT ciphertext and the encrypted App private key are never here."""
    return {
        # A legacy row may predate the write-time pointer guard. Redact URL userinfo
        # again at every served boundary so old credentials cannot reach an admin UI.
        "repository": _redact_pointer(config.repository),
        "base_ref": config.base_ref,
        "manifest_path": config.manifest_path,
        "reviewer_routing": config.reviewer_routing,
        "token_source": config.mode,
        "token_ref": config.token_ref,
        "app_id": config.app_id,
        "token_set": config.token_set,
        "updated_at": _iso(config.updated_at),
    }


def _writeback_target_view(config) -> dict:
    """One write-back target for the admin manage list (hq-095h): the single-config
    view plus the routing/management fields (id, routing_key, is_default, enabled,
    the last probe result). Like `_writeback_view` it NEVER carries a secret value
    or ciphertext -- only references and the `token_set` flag."""
    return {
        **_writeback_view(config),
        "id": config.id,
        "routing_key": config.routing_key,
        "is_default": config.is_default,
        "enabled": config.enabled,
        "test_result": config.test_result,
        "reviewer_routing": config.reviewer_routing,
    }


def _connection_view(record, *, has_observed_assets: bool = False) -> dict:
    """One observed-evidence connection for the admin list (hq-jedd). Non-secret only: the
    connector type, display name, enabled state, recorded health, and the config_ref (a base
    URL / bundle path). No credential is ever stored on a connection, so there is none to
    omit; a config_ref URL's userinfo and the health_detail are userinfo-redacted defensively
    with the canonical detector. `has_observed_assets` is the disable-only signal the admin UI
    uses to pre-disable Remove (hq-xptv)."""
    return {
        "id": record.id,
        "connector_type": record.connector_type,
        "display_name": record.display_name,
        "enabled": record.enabled,
        "health_status": record.health_status,
        "health_checked_at": _iso(record.health_checked_at),
        "health_detail": _redact_free_text_userinfo(record.health_detail),
        "config_ref": _redact_free_text_userinfo(record.config_ref),
        "has_observed_assets": has_observed_assets,
    }


def _context_source_view(row, *, conflicting_source_ids: list[str] | None = None) -> dict:
    """One Git context source for the admin list (hy-gh-75), from `read_git_context`:
    the source id, repo/ref/path, the SERVING commit (the pin), the last sync attempt, the
    validation state, and the estate DOMAIN-CONFLICT state (hq-3fjt). Git commit metadata
    only -- there is no secret to redact here. `domain_conflict` is True when another
    enabled source serves this source's domain, with `conflicting_source_ids` naming them
    -- the same ambiguity the resolver refuses on, surfaced rather than silently merged."""
    conflicts = conflicting_source_ids or []
    return {
        "source_id": row.source_id,
        "repository": row.repository,
        "ref": row.ref,
        "path": row.path,
        "display_name": row.display_name,
        "enabled": row.enabled,
        "pinned": row.pinned,
        "serving_commit": row.commit_sha,
        "committed_at": _iso(row.committed_at),
        "content_hash": row.content_hash,
        "domain": row.domain,
        "title": row.title,
        "last_attempt_status": row.last_attempt_status,
        "last_attempt_at": _iso(row.last_attempt_at),
        "last_attempted_commit": row.last_attempted_commit_sha,
        # repository/ref are RAW (credential-free by construction, the add path
        # rejects a credential pointer); last_error is the one free-text field, so
        # it is scrubbed server-side with the canonical detector (hq-kbcy round 5).
        "last_error": _redact_free_text_userinfo(row.last_error),
        "domain_conflict": bool(conflicts),
        "conflicting_source_ids": conflicts,
    }


def _source_record_view(source) -> dict:
    """A freshly registered source (hy-gh-75). Pointer + status only; no secret."""
    current = source.current_snapshot
    return {
        "source_id": source.id,
        "repository": source.repository,
        "ref": source.ref,
        "path": source.path,
        "display_name": source.display_name,
        "enabled": source.enabled,
        "serving_commit": current.commit_sha if current else None,
        "last_attempt_status": source.last_attempt_status,
    }


def _sync_result_view(result) -> dict:
    """The outcome of a sync/validate (hy-gh-75): the persisted status, the commit and
    snapshot it produced, and the validation signal -- `reasons` (why nothing was
    persisted) and `findings` (what could not be corroborated in what WAS). A `reasons`
    entry is FREE TEXT -- `str(GitReadError)` can echo the URL the read failed on, credential
    and all -- so each is scrubbed server-side with the canonical detector, exactly like
    `last_error` (hq-kbcy round 5). No secret value."""
    return {
        "source_id": result.source_id,
        "status": result.status,
        "ok": result.ok,
        "commit": result.commit_sha,
        "snapshot_id": result.snapshot_id,
        "synced_at": _iso(result.synced_at),
        "reasons": [_redact_free_text_userinfo(reason) for reason in result.reasons],
        "findings": list(result.findings),
    }


def _audit_view(entry) -> dict:
    """One admin-audit entry for the read surface (hy-gh-75): actor / action / target / time
    / result / detail. Every field is non-secret by construction -- `detail` is a target id
    or a status, never a credential."""
    return {
        "id": entry.id,
        "at": _iso(entry.at),
        "actor": entry.actor,
        "actor_issuer": entry.actor_issuer,
        "action": entry.action,
        "target": entry.target,
        "result": entry.result,
        "detail": entry.detail,
        # The id of the request that performed the action (hy-w9ntg), so the trail can be tied
        # to a response's X-Correlation-Id. Null for rows written before correlation ids.
        "correlation_id": entry.correlation_id,
    }


def _provider_probe_view(probe) -> dict:
    """One provider probe for the admin surface (hy-ng8o7). The `reason` is redacted here at the
    serving boundary -- a probe reads a key to authenticate its check and drops it, but a
    transport error string could still echo a URL with userinfo, so it is stripped."""
    return {
        "component": probe.component,
        "status": probe.status,
        "configured": probe.configured,
        "reachable": probe.reachable,
        "reason": redact_free_text_userinfo(probe.reason),
        "impact": probe.impact,
        "recovery": probe.recovery,
    }


def _diagnosis_view(row) -> dict:
    """One classified diagnosis for the admin surface (hy-bue7r). `detail`/`recovery` are
    non-secret free text, but redacted here at the serving boundary so a URL with userinfo that
    reached a `reason` upstream can never surface."""
    return {
        "class": row.diagnostic_class,
        "subject": row.subject,
        "signal": row.signal,
        "detail": redact_free_text_userinfo(row.detail),
        "recovery": redact_free_text_userinfo(row.recovery),
    }


def _audit_export_view(entry) -> dict:
    """One audit entry for the REDACTED export (hy-w9ntg): the same shape as `_audit_view`, but
    every FREE-TEXT field (`detail`, `target`) is re-run through `redact_free_text_userinfo` so
    a legacy or unredacted-at-write row can never surface URL userinfo on export. Defense in
    depth -- the write sites already redact; this makes the export safe regardless."""
    return {
        **_audit_view(entry),
        "target": redact_free_text_userinfo(entry.target),
        "detail": redact_free_text_userinfo(entry.detail),
    }


def _observed_status_view(status) -> dict:
    """One observed source's recorded status for the admin overview (hq-hnrf area 2):
    identity + configured/reachable/fresh rollup + impact/recovery. Non-secret; the free-text
    `reason`/`impact`/`recovery` are userinfo-redacted defensively at this serving boundary."""
    return {
        "connection_id": status.connection_id,
        "connector_type": status.connector_type,
        "display_name": status.display_name,
        "enabled": status.enabled,
        "status": status.status,
        "configured": status.configured,
        "reachable": status.reachable,
        "fresh": status.fresh,
        "last_health_status": status.last_health_status,
        "last_health_at": _iso(status.last_health_at),
        "last_sync_status": status.last_sync_status,
        "last_sync_finished_at": _iso(status.last_sync_finished_at),
        "reason": _redact_free_text_userinfo(status.reason),
        "impact": _redact_free_text_userinfo(status.impact),
        "recovery": _redact_free_text_userinfo(status.recovery),
    }


def _playground_enabled() -> bool:
    # Reads the settings object (loaded config, else the lenient legacy env), so the served
    # gate and the playground UI process agree on one value (hy-lk83s).
    return playground_enabled()


def _playground_index() -> Path:
    ui_root = Path(__file__).resolve().parents[2] / "playground" / "ui"
    built = ui_root / "dist" / "index.html"
    return built if built.exists() else ui_root / "index.html"


# The home page links "MCP setup" at the IN-APP wizard (hy-8u0a, V1 gap E), not an
# outbound docs URL: the onboarding surface now exists in the playground. The
# wizard itself carries the external HTTP/MCP guide link under "Need help?".
_MCP_SETUP_PATH = "/playground/mcp/"


def _home_index() -> bytes:
    """Serve the small local landing page used by the playground brand link."""
    version = __version__
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Hyperset</title>
    <style>
      :root {{
        color-scheme: light;
        font-family: ui-sans-serif, system-ui, sans-serif;
        color: #101820;
        background: #f6f7f8;
      }}
      * {{ box-sizing: border-box; }}
      body {{
        min-height: 100vh;
        margin: 0;
        display: grid;
        place-items: center;
        padding: 24px;
        background: #f6f7f8;
      }}
      main {{
        width: min(720px, 100%);
        padding: clamp(32px, 7vw, 64px);
        border: 1px solid #dfe5ea;
        border-radius: 20px;
        background: #fff;
        box-shadow: 0 18px 50px rgba(16, 24, 32, .07);
      }}
      .mark {{
        display: grid;
        place-items: center;
        width: 42px;
        height: 42px;
        border-radius: 11px;
        background: #101820;
        color: #fff;
        font-family: Georgia, serif;
        font-size: 29px;
        font-style: italic;
      }}
      .eyebrow {{
        margin: 30px 0 10px;
        color: #12395e;
        font-family: monospace;
        font-size: 11px;
        letter-spacing: .12em;
        text-transform: uppercase;
      }}
      h1 {{
        margin: 0;
        max-width: 11ch;
        overflow-wrap: anywhere;
        font-size: clamp(38px, 7vw, 64px);
        letter-spacing: -.055em;
        line-height: 1;
      }}
      p {{
        max-width: 520px;
        margin: 20px 0 28px;
        color: #66717c;
        font-size: 17px;
        line-height: 1.6;
      }}
      .links {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }}
      a {{
        display: inline-flex;
        padding: 11px 15px;
        border-radius: 8px;
        background: #12395e;
        color: #fff;
        font-size: 14px;
        font-weight: 600;
        text-decoration: none;
      }}
      a.secondary {{
        background: transparent;
        color: #12395e;
        border: 1px solid #cbd3da;
      }}
      small {{
        display: block;
        margin-top: 28px;
        color: #66717c;
        font-family: monospace;
        font-size: 10px;
      }}
    </style>
  </head>
  <body>
    <main>
      <div class="mark">h</div>
      <div class="eyebrow">Hyperset</div>
      <h1>Context that knows how it connects.</h1>
      <p>
        A governed, Git-backed context system for agents. Explore the local playground to see
        resolution, relationships, and streaming answers in action.
      </p>
      <div class="links">
        <a href="/playground/">Open playground</a>
        <a class="secondary" href="/review/">Review</a>
        <a class="secondary" href="/admin/">Settings</a>
        <a class="secondary" href="{_MCP_SETUP_PATH}">MCP setup</a>
      </div>
      <small>Hyperset API · {version}</small>
    </main>
  </body>
</html>""".encode()


def _playground_asset(path: str) -> Path | None:
    prefixes = ("/playground/assets/", "/admin/assets/")
    prefix = next((value for value in prefixes if path.startswith(value)), None)
    if prefix is None:
        return None
    ui_root = Path(__file__).resolve().parents[2] / "playground" / "ui" / "dist" / "assets"
    candidate = (ui_root / path.removeprefix(prefix)).resolve()
    try:
        candidate.relative_to(ui_root.resolve())
    except ValueError:
        return None
    return candidate if candidate.exists() and candidate.is_file() else None


def _playground_page(path: str) -> bool:
    """Identify client-side playground routes without swallowing APIs/assets."""
    for root in PLAYGROUND_ROOTS:
        prefix = f"{root}/"
        if not path.startswith(prefix):
            continue
        first_segment = path.removeprefix(prefix).split("/", 1)[0]
        return first_segment not in {"api", "assets"}
    return False


def _is_review_surface_page(path: str) -> bool:
    """The Review SURFACE's own HTML routes: the `/review` root and its client-side
    subpaths, but not its APIs or assets (hy-mg8p). This is the set the approved-reviewer
    page gate covers; every other playground page is unaffected. `path` is already the
    trailing-slash-normalized `_path()`, so `/review/` arrives as `/review`."""
    if path == REVIEW_ROOT_PATH:
        return True
    prefix = f"{REVIEW_ROOT_PATH}/"
    if not path.startswith(prefix):
        return False
    return path.removeprefix(prefix).split("/", 1)[0] not in {"api", "assets"}


def _decode_error(operation: str, exc: ValueError) -> OperationError:
    """One catch, two remedies, because the caller has two different problems.

    A shared `except` with a shared response is this project's own signature
    defect -- accurate about state, wrong about remedy. A client whose body was
    not UTF-8 does not have invalid JSON: their JSON may be perfect and their
    ENCODING is wrong, and handing them a JSON example sends them to edit a
    document that is already correct.

    THE SNIFFED CODEC IS DELIBERATELY NOT ECHOED. `json.loads` guesses an
    encoding from the first bytes, so an invalid body that happens to start
    with a BOM-like prefix reports `utf-16-le` -- a codec neither side chose.
    `str(exc)` carries it, so this builds the sentence from the offending byte
    and its position instead, and a caller reading it goes looking at their
    own bytes rather than at a phantom encoding.

    The CODE stays `invalid_json`. The served error vocabulary is gated and
    documented (hy-y633, hy-lqla), so a new code is a contract change with a
    document to amend, not something to invent at a call site -- and reusing
    this one is honest as long as the text is. If a distinct code is wanted, it
    arrives through that gate.
    """
    example = serialize(OPERATION_SPECS[operation]["example"])
    if isinstance(exc, UnicodeDecodeError):
        offending = exc.object[exc.start] if exc.start < len(exc.object) else None
        where = f"byte {offending:#04x} at position {exc.start}" if offending is not None else "it"
        return OperationError(
            INVALID_JSON,
            f"request body is not valid UTF-8: {where} cannot be decoded",
            recovery=f"send the body as UTF-8-encoded JSON, e.g. {example}",
        )
    return OperationError(
        INVALID_JSON,
        f"request body is not valid JSON: {exc}",
        recovery=f"send a JSON object, e.g. {example}",
    )


def _route_error(path: str) -> OperationError:
    return OperationError(
        UNKNOWN_ROUTE,
        f"{path!r} is not served by this server",
        recovery=(
            f"POST to one of: {', '.join(sorted(ROUTES))}; "
            f"GET {CONTEXT_HISTORY_PATH} for read-only context snapshot history"
        ),
    )


def _method_error(path: str) -> OperationError:
    return OperationError(
        METHOD_NOT_ALLOWED,
        f"{path!r} answers POST, not GET",
        recovery=f"POST a JSON object to {path}",
    )


def build_server(
    *, session_factory, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT
) -> ThreadingHTTPServer:
    """Bind the server without serving, so callers (and tests) own the loop."""
    from hyperset.transport.review_runtime import install_authoring_runtime

    server = ThreadingHTTPServer((host, port), _Handler)
    server.session_factory = session_factory
    # Inject the refine model runtime (hy-jis1): operations names no inference
    # module, so the served refine op gets its runtime here.
    install_authoring_runtime()
    return server


def serve_http(*, session_factory, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    server = build_server(session_factory=session_factory, host=host, port=port)
    print(f"hyperset HTTP on {host}:{server.server_address[1]} serving {', '.join(OPERATIONS)}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
