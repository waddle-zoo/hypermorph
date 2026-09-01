import React, { useEffect, useMemo, useRef, useState } from "react";
import DOMPurify from "dompurify";
import { marked } from "marked";
import "./styles.css";

export function createHypersetClient({ apiRoot = "/v0", fetchImpl = globalThis.fetch } = {}) {
  const root = String(apiRoot || "").replace(/\/+$/, "");
  return {
    streamChat(payload, { signal } = {}) {
      return fetchImpl(`${root}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify(payload),
        signal,
      });
    },
  };
}

// Recent-threads local storage (hy-87n1). A completed turn is kept with the run settings
// that produced it -- agent / model / governed-only -- so Recent threads restores the
// thread's STATE, not just the question text. Browser-local convenience only: a bad or
// full store never breaks a chat.
export const THREADS_KEY = "hyperset-threads";
export const THREAD_RESTORE_KEY = "hyperset-thread-restore";
const THREAD_LIMIT = 30;

// Keep the immutable trust disclosure when a recent chat is reopened, without turning
// browser-local history into a copy of the whole server response. This is an explicit
// allowlist: the answer text/settings remain useful, while provenance fields consumed by
// TrustPanel survive (bundle, authority, resolution, policy, provider/model, trace). SQL,
// raw request payloads, and other transient fields are intentionally not persisted.
function persistedAssistantEvidence(message) {
  if (message?.role !== "assistant") return {};
  const sourceResult = message.result && typeof message.result === "object" ? message.result : {};
  const sourceBundle = message.bundle && typeof message.bundle === "object" ? message.bundle : {};
  const result = {};
  const bundle = {};
  ["bundle_id", "context_resolution", "agent_label", "provider", "model", "trace"].forEach((key) => {
    if (sourceResult[key] !== undefined && sourceResult[key] !== null) result[key] = sourceResult[key];
  });
  if (sourceResult.agent_config?.policy_result !== undefined) {
    result.agent_config = { policy_result: sourceResult.agent_config.policy_result };
  }
  ["bundle_id", "resolution", "context_authority", "linked_evidence"].forEach((key) => {
    if (sourceBundle[key] !== undefined && sourceBundle[key] !== null) bundle[key] = sourceBundle[key];
  });
  return redactDeep({
    ...(Object.keys(result).length ? { result } : {}),
    ...(Object.keys(bundle).length ? { bundle } : {}),
  });
}

export function saveThreadTurn(storage, turn, { limit = THREAD_LIMIT } = {}) {
  let previous = [];
  try {
    const parsed = JSON.parse(storage.getItem(THREADS_KEY) || "[]");
    if (Array.isArray(parsed)) previous = parsed;
  } catch { previous = []; }
  // Redact URL userinfo at the PERSIST boundary (hy-87n1 critic; the #472 lesson): chat
  // question/answer are FREE-FORM (a user can paste a `scheme://user:token@host` URL), so
  // the record is scrubbed with the canonical deep redactor BEFORE it reaches localStorage
  // -- no credential is ever written in cleartext, and a reopened thread can never read one
  // back. `governedOnly` / numbers pass through redactDeep unchanged.
  const record = redactDeep({
    id: turn.id,
    question: turn.question,
    answer: turn.answer,
    createdAt: turn.createdAt,
    // The replayable run settings. `governedOnly` defaults to the governed-only default
    // for a legacy record that predates this field, so an old thread restores safely.
    agent: turn.agent ?? null,
    model: turn.model ?? null,
    governedOnly: turn.governedOnly !== false,
    messages: (Array.isArray(turn.messages) ? turn.messages : [
      { role: "user", content: turn.question, createdAt: turn.createdAt },
      { role: "assistant", content: turn.answer, createdAt: turn.createdAt },
    ]).filter((message) => (message?.role === "user" || message?.role === "assistant") && message.content).map((message) => ({
      id: message.id,
      role: message.role,
      content: message.content,
      createdAt: message.createdAt,
      ...persistedAssistantEvidence(message),
    })),
  });
  const next = [record, ...previous.filter((thread) => thread && thread.id !== turn.id)].slice(0, limit);
  try { storage.setItem(THREADS_KEY, JSON.stringify(next)); } catch { /* quota: history is a convenience */ }
  return next;
}

export function writeThreadRestore(storage, restore) {
  // Same persist-boundary redaction as saveThreadTurn: the reopen handoff carries the
  // (free-form) question, so scrub URL userinfo before it reaches localStorage.
  try { storage.setItem(THREAD_RESTORE_KEY, JSON.stringify(redactDeep(restore))); } catch { /* convenience only */ }
}

export function readThreadRestore(storage) {
  try {
    const raw = storage.getItem(THREAD_RESTORE_KEY);
    if (!raw) return null;
    storage.removeItem(THREAD_RESTORE_KEY); // read-once: a plain refresh must not re-restore
    const parsed = JSON.parse(raw);
    // Defense in depth: redact on read too, so a handoff written by older code (or hand-
    // edited storage) can never surface a credential into the restored state.
    return parsed && typeof parsed === "object" ? redactDeep(parsed) : null;
  } catch { return null; }
}

function escapeHtml(value) {
  return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function highlightSqlHtml(sql) {
  return tokenizeSql(sql)
    .map(([type, text]) => (type === "x" || type === "id" ? escapeHtml(text) : `<span class="tok-${type}">${escapeHtml(text)}</span>`))
    .join("");
}

// A marked renderer that ships syntax-highlighted code and a copy button as part
// of the parsed HTML, so it survives React re-renders (a useEffect that mutates
// dangerouslySetInnerHTML output gets wiped on the next streamed token).
const markdownRenderer = new marked.Renderer();
markdownRenderer.code = function code(token, infostring) {
  const text = typeof token === "object" && token !== null ? token.text : String(token ?? "");
  const lang = ((typeof token === "object" && token !== null ? token.lang : infostring) || "").trim();
  const isSql = /^sql$/i.test(lang) || /\b(SELECT|INSERT|UPDATE|DELETE|WITH|CREATE)\b/i.test(text);
  const body = isSql ? highlightSqlHtml(text) : escapeHtml(text);
  return `<pre><code class="language-${escapeHtml(lang)}">${body}</code><button class="code-copy" type="button">Copy</button></pre>`;
};

function Markdown({ children }) {
  const html = useMemo(
    () => DOMPurify.sanitize(marked.parse(String(children || ""), { breaks: true, renderer: markdownRenderer })),
    [children],
  );
  const onClick = (event) => {
    const button = event.target.closest?.(".code-copy");
    if (!button) return;
    const code = button.parentElement.querySelector("code");
    navigator.clipboard?.writeText(code?.textContent || "").then(() => { button.textContent = "Copied"; setTimeout(() => { button.textContent = "Copy"; }, 1500); }).catch(() => {});
  };
  return <div className="markdown" onClick={onClick} dangerouslySetInnerHTML={{ __html: html }} />;
}

export { Markdown };

function formatTime(value = Date.now()) {
  return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(value);
}

function makeMessage(role, content = "") {
  return {
    id: globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`,
    role,
    content,
    createdAt: Date.now(),
    stages: [],
    bundle: null,
    selection: null,
    sql: null,
    result: null,
    resolutionError: null,
    status: role === "assistant" ? "ready" : "sent",
  };
}

function SelectField({ label, value, onChange, options, disabled = false }) {
  return (
    <label className="select-field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange?.(event.target.value)} disabled={disabled || options.length === 0}>
        {options.length ? options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>) : <option value="">Unavailable</option>}
      </select>
    </label>
  );
}

export function AgentControls({ admin, agent, model, setAgent, setModel, agents = [], models = [], backendHealthy, embedded = false }) {
  // Redact the selected agent at the data boundary so its free-text `detail` (server
  // config) cannot render a credential URL (hy-6tsw9 #452).
  const selectedAgent = redactDeep(agents.find((item) => item.value === agent));
  return (
    <section className={admin ? "control-panel admin-controls" : embedded ? "composer-agent-controls" : "control-strip"}>
      <div className="control-copy">
        <span className="eyebrow">{admin ? "Operator workspace" : "Agent workspace"}</span>
        <strong>{admin ? "Inspect every step" : "Ask the governed graph"}</strong>
        <p>{selectedAgent?.detail || (backendHealthy ? "The backend has not published an agent configuration." : "Waiting for a healthy Hyperset backend connection.")}</p>
      </div>
      <div className="control-fields">
        <SelectField label="Agent" value={agent} onChange={setAgent} options={agents} disabled={!backendHealthy} />
        <SelectField label="Model" value={model} onChange={setModel} options={models} disabled={!backendHealthy} />
      </div>
      {admin && (
        <div className="policy-note">
          <span className="policy-dot" />
          <div><b>Context is agent-resolved</b><small>Bundles are never user-selected. Every turn discovers the connected evidence it needs.</small></div>
        </div>
      )}
    </section>
  );
}

function formatDuration(ms) {
  const seconds = Math.max(1, Math.round(ms / 1000));
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

// One consolidated view of the agent's process: a live line while it works, a
// collapsed "Worked for Ns" disclosure once the answer arrives. Everything the
// old stage list, resolution notice, and observability box showed lives here.
function WorkSummary({ message, admin }) {
  const stages = message.stages || [];
  const hasDetail = message.bundle || message.resolutionError || (message.result?.trace?.length > 0);
  if (!stages.length && !hasDetail) return null;
  const streaming = message.status === "streaming";
  const last = stages[stages.length - 1];
  const durationMs = (message.finishedAt || Date.now()) - message.createdAt;
  if (streaming) {
    return <div className="work-live-row" role="status" aria-live="polite"><span className="work-spinner" aria-hidden="true" /><span>{last?.title || "Working through the governed context…"}</span><small>Details appear after the answer.</small></div>;
  }
  return (
    <details className="work-summary" open={false}>
      <summary>
        <><span className="work-chevron" aria-hidden="true" />Worked for {formatDuration(durationMs)} · show run details</>
      </summary>
      <div className="work-summary-body">
        {stages.map((stage, index) => (
          <div className={`work-step ${stage.status === "warning" ? "warning" : ""} ${streaming && index === stages.length - 1 ? "active" : ""}`} key={`${stage.stage}-${index}`}>
            <span className="work-step-dot" />
            <div><b>{stage.title}</b>{stage.detail && <small>{redactUserinfo(stage.detail)}</small>}</div>
          </div>
        ))}
        {message.bundle && <GraphSummary bundle={message.bundle} selection={message.selection} />}
        {message.bundle && <details className="json-details"><summary>Resolved bundle payload</summary><pre>{redactUserinfo(JSON.stringify({ bundle_id: message.bundle.bundle_id, resolution: message.bundle.resolution, authority: message.bundle.context_authority, directive: message.bundle.request?.directive }, null, 2))}</pre></details>}
        {message.resolutionError && <details className="json-details"><summary>Context resolution details</summary><pre>{redactUserinfo(JSON.stringify(message.resolutionError, null, 2))}</pre></details>}
        {message.result?.trace?.length > 0 && <details className="json-details"><summary>Trace and validations</summary><pre>{redactUserinfo(JSON.stringify(message.result.trace, null, 2))}</pre></details>}
      </div>
    </details>
  );
}

function GraphSummary({ bundle, selection }) {
  const graph = bundle?.domain_graph || {};
  const nodes = Array.isArray(graph.nodes) ? graph.nodes : [];
  const edges = Array.isArray(graph.edges) ? graph.edges : [];
  const concepts = bundle?.request?.directive?.concepts || selection?.directive?.concepts || [];
  const domain = bundle?.request?.directive?.domains?.[0] || selection?.directive?.domains?.[0] || "governed context";
  const labels = [domain, ...concepts, "Superset", "DataHub", "Git authority"];
  const distinctLabels = [...new Set([...nodes.map((node) => node.label || node.name || node.id), ...labels].filter(Boolean))].slice(0, 7);
  return (
    <div className="graph-card">
      <div className="graph-heading"><span className="mini-label">ContextBundle</span><span className="graph-count">{edges.length || "connected"} relationships</span></div>
      <div className="graph-path">
        {distinctLabels.map((label, index) => <React.Fragment key={`${label}-${index}`}><span className={index === 0 ? "graph-node accent" : "graph-node"}>{label}</span>{index < distinctLabels.length - 1 && <span className="graph-edge">→</span>}</React.Fragment>)}
      </div>
      <small className="graph-caption">Resolved from the question; related bundles and observed connections stay visible to the agent.</small>
    </div>
  );
}

const SQL_KEYWORDS = new Set(["SELECT","FROM","WHERE","JOIN","LEFT","RIGHT","INNER","OUTER","FULL","CROSS","ON","AND","OR","NOT","AS","GROUP","BY","ORDER","HAVING","LIMIT","OFFSET","SUM","COUNT","AVG","MIN","MAX","DISTINCT","WITH","UNION","ALL","CASE","WHEN","THEN","ELSE","END","IN","IS","NULL","TRUE","FALSE","BETWEEN","LIKE","ILIKE","COALESCE","CAST","OVER","PARTITION","ASC","DESC","EXISTS","INTERVAL","DATE","EXTRACT"]);

// Tokenise into typed [type, text] pairs so React renders coloured spans with
// no HTML injection. Deliberately small: keywords, strings, numbers, comments.
function tokenizeSql(sql) {
  const tokens = [];
  const re = /('(?:[^']|'')*')|(--[^\n]*)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)|(\s+)|([^\s\w]+)/g;
  let match;
  while ((match = re.exec(sql)) !== null) {
    if (match[1]) tokens.push(["str", match[1]]);
    else if (match[2]) tokens.push(["com", match[2]]);
    else if (match[3]) tokens.push(["num", match[3]]);
    else if (match[4]) tokens.push([SQL_KEYWORDS.has(match[4].toUpperCase()) ? "kw" : "id", match[4]]);
    else tokens.push(["x", match[0]]);
  }
  return tokens;
}

function SqlResult({ sql }) {
  const [copied, setCopied] = useState(false);
  if (!sql) return null;
  const rows = sql.rows || [];
  const columns = sql.columns || (rows[0] ? Object.keys(rows[0]) : []);
  const query = sql.sql || "-- no query";
  const copy = (event) => {
    event.preventDefault();
    navigator.clipboard?.writeText(query).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); }).catch(() => {});
  };
  return (
    <details className="sql-card">
      <summary>
        <span><i className="summary-dot" />Data used</span>
        <span className="sql-card-actions">
          <small>{sql.error ? "query failed" : `${sql.row_count ?? rows.length} rows returned`}</small>
          <button type="button" className="sql-copy" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
        </span>
      </summary>
      <pre><code>{tokenizeSql(query).map(([type, text], index) => type === "x" || type === "id" ? text : <span key={index} className={`tok-${type}`}>{text}</span>)}</code></pre>
      {sql.error ? <p className="error-text">{redactUserinfo(sql.error)}</p> : rows.length > 0 && (
        <div className="result-table-wrap"><table><thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{rows.slice(0, 8).map((row, index) => <tr key={index}>{columns.map((column) => <td key={column}>{String(row[column] ?? "—")}</td>)}</tr>)}</tbody></table></div>
      )}
    </details>
  );
}

function UserCopy({ content, attachments }) {
  const labels = new Set((attachments || []).map((item) => item.label));
  const parts = String(content).split(/(@[^\s@]+)/g);
  return (
    <p className="user-copy">
      {parts.map((part, index) =>
        part.startsWith("@") && labels.has(part.slice(1))
          ? <span key={index} className="ref-pill">{part}</span>
          : part,
      )}
    </p>
  );
}

export function GovernedBlocked({ blocked, result, disabled, onContinue }) {
  if (!blocked) return null;
  // A provider/config fault on the context-discovery call is NOT an empty catalog
  // (GitHub #344). Rendering the corpus-blaming "No governed metadata found" for it
  // swallowed the real cause (a bad base URL / model / credential) and pointed the
  // operator at their governed context instead of their config (hy-yts5j). The
  // backend already attributes it: context_source === "discovery_provider_error",
  // with the real provider error carried in context_resolution.error.detail.
  const providerFault = result?.context_source === "discovery_provider_error";
  if (providerFault) {
    // Redact the whole error at the boundary so message/detail/recovery are all clean
    // regardless of shape (hy-6tsw9 #452).
    const err = redactDeep(result?.context_resolution?.error || {});
    return (
      <div className="governed-blocked provider-fault">
        <div className="governed-blocked-heading"><span className="notice-icon">!</span><b>Context discovery failed at the model provider</b></div>
        <p>{err.message || "The model provider failed the context-discovery call, so no governed context could be requested. This is a model/provider configuration fault, not an empty catalog."}</p>
        {err.detail && <p className="provider-fault-detail"><small>Provider error: {err.detail}</small></p>}
        {err.recovery && <p className="provider-fault-recovery"><small>{err.recovery}</small></p>}
        <button type="button" className="governed-continue" disabled={disabled} onClick={() => onContinue(blocked)}>Continue without governed context</button>
      </div>
    );
  }
  return (
    <div className="governed-blocked">
      <div className="governed-blocked-heading"><span className="notice-icon">!</span><b>No governed metadata found</b></div>
      <p>Governed-only is on, so no answer was produced. You can continue without governed context — the answer will not be backed by any governed source and cannot be trusted as governed.</p>
      <button type="button" className="governed-continue" disabled={disabled} onClick={() => onContinue(blocked)}>Continue without governed context</button>
    </div>
  );
}

// First-class per-answer trust state, keyed by the served resolution status
// (hyperset.bundle.schema RESOLUTION_STATUSES). `observed_only` and `no_match`
// are NOT governed-trusted, so each carries a next action, not just a label --
// the immutable trust/provenance disclosure the V1 contract requires instead of
// the collapsed JSON the run-details used to hide it in (hy-icx1, Explorer 5+7).
export const TRUST_STATES = {
  governed: {
    label: "Governed",
    tone: "ready",
    note: "Backed by a governed ContextBundle from the customer's Git authority.",
  },
  mixed: {
    label: "Governed + observed",
    tone: "ready",
    note: "Governed context, corroborated with observed evidence from connected systems.",
  },
  observed_only: {
    label: "Observed only",
    tone: "warn",
    note: "No governed context matched; this answer rests on raw observed metadata and is NOT governed-trusted.",
    action: "Open Explore context or refine the question to pull in a governed bundle before trusting this.",
  },
  no_match: {
    label: "No governed match",
    tone: "warn",
    note: "The agent abstained: nothing in the governed catalog matched the question.",
    action: "Refine the question, or open Explore context to find a related governed domain.",
  },
};

function isStaleOrConflict(code) {
  return /stale|conflict/i.test(String(code || ""));
}

// The ONE render-boundary redactor for EVERY user-facing free-text field the chat
// shows (hy-6tsw9). The server redacts URL userinfo where each field is built
// (schema.warning, _provider_error_payload), but the credential-in-free-text class
// cost #447/#448/#449 by leaking through a NEW render, so every free-text render here
// -- stage.detail, sql.error, message.error, the provider detail, the warning message,
// and the WorkSummary JSON dumps -- passes through this one helper, and
// `test_chat_ui_contract` fails if a new bare free-text interpolation bypasses it. The
// EXACT canonical `scheme://userinfo@` rule the server uses (#431/#447): the `@` before
// the first `/`, so a port + git revision or an scp `git@host:path` are preserved. Pure
// string replace -- never throws; a non-string (e.g. undefined) passes through.
const URL_USERINFO_G = /([a-zA-Z][a-zA-Z0-9+.\-]*:\/\/)[^/]*@/g;

function redactUserinfo(value) {
  return typeof value === "string" ? value.replace(URL_USERINFO_G, "$1") : value;
}

// Redact URL userinfo from EVERY string in a value, whatever its shape (hy-6tsw9
// #452). This is the DATA/PROPS-boundary choke point: a component redacts the whole
// server payload as it enters render, so NO downstream JSX interpolation shape --
// `{x.content}`, a bare `{error}`, a template, a nested/spaced `JSON.stringify` --
// can leak a credential, and the invariant is closed by construction rather than
// policed by a source regex. Returns a redacted COPY; non-strings pass through.
export function redactDeep(value) {
  if (typeof value === "string") return redactUserinfo(value);
  if (Array.isArray(value)) return value.map(redactDeep);
  if (value && typeof value === "object") {
    const out = {};
    for (const [key, inner] of Object.entries(value)) out[key] = redactDeep(inner);
    return out;
  }
  return value;
}

function isTimeout(message) {
  return (
    message.status === "error" &&
    /time(?:d)?\s*out|timeout|time budget|exceeded its .* timeout/i.test(String(message.error || ""))
  );
}

// The immutable trust/provenance panel shown on every completed answer: the
// effective trust state + next action, the bundle id, the Git authority commit,
// the resolution warnings (stale/conflict called out with a next action), and the
// agent/provider/model/policy that produced it. A timeout is its own labeled state.
export function TrustPanel({ message }) {
  const result = message.result || null;
  const bundle = message.bundle || null;
  const timedOut = isTimeout(message);
  const status = bundle?.resolution?.status || result?.context_resolution?.status;
  const state = TRUST_STATES[status];
  // A governed-blocked / provider fault (no bundle, no status) is covered by
  // GovernedBlocked; nothing to disclose here unless it timed out.
  if (!state && !timedOut) return null;
  const bundleId = result?.bundle_id || bundle?.bundle_id;
  const authority = bundle?.context_authority || {};
  const warnings = bundle?.resolution?.warnings || [];
  const conflicts = bundle?.linked_evidence?.conflicts || [];
  const policy = result?.agent_config?.policy_result;
  return (
    <div className={`trust-panel ${timedOut ? "warn" : state?.tone || ""}`} role="status">
      <div className="trust-heading">
        <span className="mini-label">Trust</span>
        <b className="trust-state">{timedOut ? "Timed out" : state.label}</b>
      </div>
      <p className="trust-note">
        {timedOut
          ? "The turn exceeded its execution time budget, so no governed answer completed."
          : state.note}
      </p>
      {(timedOut || state.action) && (
        <p className="trust-action">
          <b>Next:</b>{" "}
          {timedOut
            ? "Retry the question, or narrow it and lower reasoning effort, then send again."
            : state.action}
        </p>
      )}
      {!timedOut && (
        <dl className="trust-provenance">
          {bundleId && <div><dt>Bundle ID</dt><dd>{bundleId}</dd></div>}
          {authority.commit_sha && (
            <div><dt>Git authority</dt><dd>{authority.path ? `${authority.path} @ ` : ""}{authority.commit_sha}</dd></div>
          )}
          {result?.agent_label && <div><dt>Agent</dt><dd>{result.agent_label}</dd></div>}
          {result?.provider && (
            <div><dt>Provider / model</dt><dd>{result.provider}{result.model ? ` · ${result.model}` : ""}</dd></div>
          )}
          {policy && <div><dt>Policy</dt><dd>{policy}</dd></div>}
        </dl>
      )}
      {warnings.length > 0 && (
        <ul className="trust-warnings">
          {warnings.map((warning, index) => (
            <li key={`${warning.code}-${index}`} className={isStaleOrConflict(warning.code) ? "trust-warning stale-conflict" : "trust-warning"}>
              <span className="trust-warning-code">{warning.code}</span> {redactUserinfo(warning.message)}
              {isStaleOrConflict(warning.code) && (
                <span className="trust-warning-action"> — re-sync the source or reconcile the conflict before relying on this.</span>
              )}
            </li>
          ))}
        </ul>
      )}
      {conflicts.length > 0 && (
        <p className="trust-conflict"><b>Conflict:</b> {conflicts.length} governed/observed conflict(s) — reconcile in Review before trusting this answer.</p>
      )}
    </div>
  );
}

export function Message({ message, admin, onContinue, continueDisabled }) {
  // DATA/PROPS-boundary redaction (hy-6tsw9 #452): redact the WHOLE message once, here,
  // then render only from the redacted copy `m`. Every free-text it carries -- the LLM
  // ANSWER (`content`), stage details, sql/errors, the provider fault, the bundle/trace
  // dumps, warnings -- is scrubbed of `scheme://user:token@host` regardless of the JSX
  // shape it renders in, so no render can reopen the #447/#448/#449 credential class. The
  // LLM answer is redacted like any other rendered text (a model can echo a credential).
  const m = redactDeep(message);
  const isAssistant = m.role === "assistant";
  const streaming = m.status === "streaming";
  return (
    <article className={`message ${m.role} ${m.status}`}>
      <div className="message-meta"><span className="avatar">{m.role === "user" ? "you" : "h"}</span><b>{m.role === "user" ? "You" : "Hyperset"}</b><time>{formatTime(m.createdAt)}</time>{isAssistant && m.result?.agent_label && <span className="agent-tag">{m.result.agent_label}</span>}</div>
      {isAssistant && m.status === "queued" && <div className="chat-queued"><span className="work-spinner" aria-hidden="true" /><span>Queued{typeof m.queuePosition === "number" && m.queuePosition > 0 ? ` · ${m.queuePosition} ahead` : "…"}</span></div>}
      {isAssistant && <WorkSummary message={m} admin={admin} />}
      {isAssistant && streaming && !m.content && !(m.stages || []).length && <div className="thinking"><span /><span /><span /> working</div>}
      {m.content && (isAssistant ? <Markdown>{m.content}</Markdown> : <UserCopy content={m.content} attachments={m.attachments} />)}
      {isAssistant && m.sql && !streaming && <SqlResult sql={m.sql} />}
      {isAssistant && !streaming && <TrustPanel message={m} />}
      {isAssistant && m.status === "cancelled" && <div className="chat-cancelled">Stopped in your browser. Context discovery already running may still finish on the server.</div>}
      {isAssistant && <GovernedBlocked blocked={m.blocked} result={m.result} disabled={continueDisabled} onContinue={onContinue} />}
      {m.status === "error" && <div className="error-banner">{m.error || "The agent could not complete this turn."}</div>}
    </article>
  );
}

function attachmentKey(item) { return `${item.kind}:${item.domain}:${item.term || ""}`; }

function hasInlineToken(value, label) {
  const token = `@${label}`;
  const start = String(value).indexOf(token);
  if (start < 0) return false;
  const before = start === 0 ? " " : String(value)[start - 1];
  const after = String(value)[start + token.length];
  return /\s/.test(before) && (!after || /\s/.test(after));
}

function removeInlineToken(value, label, caret = String(value).length) {
  const text = String(value);
  const token = `@${label}`;
  const start = text.lastIndexOf(token, Math.max(0, caret));
  if (start < 0) return null;
  const before = start === 0 ? " " : text[start - 1];
  const after = text[start + token.length];
  if (!/\s/.test(before) || (after && !/\s/.test(after))) return null;
  const next = (text.slice(0, start) + text.slice(start + token.length).replace(/^\s+/, "")).trimStart();
  return { value: next, caret: Math.min(start, next.length) };
}

function inlineTokenAtCaret(value, label, caret) {
  const text = String(value);
  const token = `@${label}`;
  const start = text.lastIndexOf(token, Math.max(0, caret));
  if (start < 0) return false;
  const end = start + token.length;
  return caret >= end && /^\s*$/.test(text.slice(end, caret));
}

export function AssetSearch({ apiRoot, attachments, onAttach, onClose, externalQuery, hideInput = false }) {
  const inlineMode = externalQuery !== undefined;
  const [items, setItems] = useState(null);
  const [internalQuery, setInternalQuery] = useState("");
  const [error, setError] = useState("");
  const inputRef = useRef(null);
  const panelRef = useRef(null);
  const query = inlineMode ? externalQuery : internalQuery;
  useEffect(() => { if (!hideInput) inputRef.current?.focus(); }, [hideInput]);
  useEffect(() => {
    if (inlineMode) return undefined; // inline mode is driven by typing; it closes itself
    const onDown = (event) => {
      if (panelRef.current && !panelRef.current.contains(event.target) && !event.target.closest?.(".attach-button")) onClose();
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [onClose, inlineMode]);
  useEffect(() => {
    let live = true;
    fetch(`${apiRoot}/v0/list_context_catalog`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ limit: 200, offset: 0 }) })
      .then((response) => response.json())
      .then((data) => {
        if (!live) return;
        const flat = [];
        for (const entry of data.domains || []) {
          flat.push({ kind: "domain", domain: entry.domain, term: "", label: entry.domain, detail: entry.title || "governed domain" });
          for (const concept of entry.concepts || []) flat.push({ kind: "concept", domain: entry.domain, term: concept, label: concept, detail: `concept in ${entry.domain}` });
        }
        setItems(redactDeep(flat));  // data-boundary redaction (hy-6tsw9 #452)
      })
      .catch((reason) => { if (live) setError(redactUserinfo(reason.message) || "Could not load the governed catalog."); });
    return () => { live = false; };
  }, [apiRoot]);
  const attached = new Set(attachments.map(attachmentKey));
  const needle = query.trim().toLowerCase();
  const results = (items || []).filter((item) => !needle || item.label.toLowerCase().includes(needle) || item.domain.toLowerCase().includes(needle) || item.detail.toLowerCase().includes(needle)).slice(0, 30);
  return (
    <div className="asset-search" role="dialog" aria-label="Search governed assets" ref={panelRef}>
      {!hideInput && (
        <div className="asset-search-head">
          <input ref={inputRef} value={internalQuery} onChange={(event) => setInternalQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") onClose(); }} placeholder="Search governed domains and concepts…" />
          <button type="button" className="asset-search-close" onClick={onClose} aria-label="Close search">✕</button>
        </div>
      )}
      {error && <div className="asset-search-empty">{error}</div>}
      {!error && items === null && <div className="asset-search-empty">Loading governed catalog…</div>}
      {!error && items !== null && (
        <div className="asset-search-results">
          {results.length === 0 && <div className="asset-search-empty">No governed assets match.</div>}
          {results.map((item) => {
            const isAttached = attached.has(attachmentKey(item));
            return (
              <button type="button" key={attachmentKey(item)} className={`asset-search-item ${isAttached ? "attached" : ""}`} disabled={isAttached} onClick={() => onAttach(item)}>
                <span className={`asset-kind ${item.kind}`}>{item.kind}</span>
                <span className="asset-label"><b>{item.label}</b><small>{redactUserinfo(item.detail)}</small></span>
                <span className="asset-add">{isAttached ? "added" : "+"}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// Split text into plain runs and @token runs so tagged tokens can be highlighted.
function highlightParts(value, inlineLabels) {
  return String(value).split(/(@[^\s@]+)/g).map((part, index) =>
    part.startsWith("@") && inlineLabels.has(part.slice(1))
      ? <mark key={index} className="ref-highlight">{part}</mark>
      : <span key={index}>{part}</span>,
  );
}

export function Composer({ onSend, sendDisabled, busy = false, onCancel, admin, agent, model, setAgent, setModel, agents, models, backendHealthy, apiRoot, initialQuestion = "", initialGovernedOnly = true }) {
  const [value, setValue] = useState(initialQuestion);
  const [governedOnly, setGovernedOnly] = useState(initialGovernedOnly !== false);
  const [attachments, setAttachments] = useState([]);
  const [pinOpen, setPinOpen] = useState(false);
  const [mention, setMention] = useState(null); // { start, query } while typing an @reference
  const textareaRef = useRef(null);
  const mirrorRef = useRef(null);
  const inlineLabels = new Set(attachments.filter((item) => item.inline).map((item) => item.label));
  useEffect(() => { if (initialQuestion) setValue(initialQuestion); }, [initialQuestion]);
  // Restore the run mode a reopened thread was run with (hy-87n1); harmless on a fresh
  // mount where it stays the governed-only default.
  useEffect(() => { setGovernedOnly(initialGovernedOnly !== false); }, [initialGovernedOnly]);

  const addAttachment = (item, inline) => setAttachments((current) => current.some((existing) => attachmentKey(existing) === attachmentKey(item)) ? current : [...current, { kind: item.kind, domain: item.domain, term: item.term, label: item.label, inline }]);

  // Detect the @token the caret currently sits inside (an @ followed by non-space).
  const syncMention = (text, caret) => {
    const match = text.slice(0, caret).match(/@([^\s@]*)$/);
    setMention(match ? { start: caret - match[0].length, query: match[1] } : null);
  };
  const onChange = (event) => {
    const text = event.target.value;
    setValue(text);
    setPinOpen(false);
    syncMention(text, event.target.selectionStart);
    // Drop an inline tag the moment its @token is edited out of the text.
    setAttachments((current) => current.filter((item) => !item.inline || hasInlineToken(text, item.label)));
  };
  const syncScroll = () => { if (mirrorRef.current && textareaRef.current) mirrorRef.current.scrollTop = textareaRef.current.scrollTop; };

  const submit = () => {
    const next = value.trim();
    if (!next || sendDisabled) return;
    // Drop inline refs whose @token the user deleted from the text; keep pinned ones.
    const active = attachments.filter((item) => !item.inline || value.includes(`@${item.label}`));
    onSend(next, { governedOnly, attachments: active });
    setValue(""); setAttachments([]); setMention(null); setPinOpen(false);
  };

  const pickInline = (item) => {
    if (!mention) return;
    const token = `@${item.label} `;
    const before = value.slice(0, mention.start);
    const after = value.slice(mention.start + 1 + mention.query.length);
    const next = before + token + after;
    addAttachment(item, true);
    setValue(next);
    setMention(null);
    const pos = before.length + token.length;
    requestAnimationFrame(() => { const ta = textareaRef.current; if (ta) { ta.focus(); ta.setSelectionRange(pos, pos); } });
  };
  const pin = (item) => { addAttachment(item, false); setPinOpen(false); };
  const removeAttachment = (item) => {
    if (!item.inline) {
      setAttachments((current) => current.filter((existing) => attachmentKey(existing) !== attachmentKey(item)));
      return;
    }
    const removed = removeInlineToken(value, item.label, textareaRef.current?.selectionStart ?? value.length);
    setAttachments((current) => current.filter((existing) => attachmentKey(existing) !== attachmentKey(item)));
    if (!removed) return;
    setValue(removed.value);
    setMention(null);
    requestAnimationFrame(() => { const ta = textareaRef.current; if (ta) { ta.focus(); ta.setSelectionRange(removed.caret, removed.caret); } });
  };
  const handleKeyDown = (event) => {
    if (event.key === "Escape" && mention) { setMention(null); return; }
    if (event.key === "Backspace" && !event.shiftKey && !event.metaKey && !event.ctrlKey) {
      const ta = textareaRef.current;
      const caret = ta?.selectionStart ?? value.length;
      const selectionEnd = ta?.selectionEnd ?? caret;
      const inline = [...attachments].reverse().find((item) => item.inline && hasInlineToken(value, item.label) && inlineTokenAtCaret(value, item.label, caret));
      const removed = inline && selectionEnd === caret ? removeInlineToken(value, inline.label, caret) : null;
      if (removed) {
        event.preventDefault();
        setValue(removed.value);
        setAttachments((current) => current.filter((item) => attachmentKey(item) !== attachmentKey(inline)));
        setMention(null);
        requestAnimationFrame(() => { const input = textareaRef.current; if (input) input.setSelectionRange(removed.caret, removed.caret); });
        return;
      }
    }
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); submit(); }
  };
  const pinnedChips = attachments.filter((item) => !item.inline);
  const inlineChips = attachments.filter((item) => item.inline);
  return (
    <div className="composer-wrap">
      {mention && <AssetSearch apiRoot={apiRoot} attachments={attachments} onAttach={pickInline} onClose={() => setMention(null)} externalQuery={mention.query} hideInput />}
      {pinOpen && !mention && <AssetSearch apiRoot={apiRoot} attachments={attachments} onAttach={pin} onClose={() => setPinOpen(false)} />}
      <div className="composer-panel">
        {inlineChips.length > 0 && <div className="composer-context-row" aria-label="Inline governed context"><span className="composer-context-label">Context</span>{inlineChips.map((item) => <span className="attach-chip inline" key={attachmentKey(item)}><span className={`asset-kind ${item.kind}`}>{item.kind}</span>{item.label}<button type="button" onClick={() => removeAttachment(item)} aria-label={`Remove ${item.label} context`}>×</button></span>)}</div>}
        {pinnedChips.length > 0 && (
          <div className="attach-chips" aria-label="Pinned governed assets">
            {pinnedChips.map((item) => (
              <span className="attach-chip" key={attachmentKey(item)}>
                <span className={`asset-kind ${item.kind}`}>{item.kind}</span>
                {item.label}
                <button type="button" onClick={() => removeAttachment(item)} aria-label={`Remove ${item.label}`}>✕</button>
              </span>
            ))}
          </div>
        )}
        <div className="composer">
          <div className="composer-input">
            <div className="composer-highlight" aria-hidden="true" ref={mirrorRef}>{highlightParts(value, inlineLabels)}{"​"}</div>
            <textarea ref={textareaRef} value={value} onChange={onChange} onScroll={syncScroll} onKeyDown={handleKeyDown} placeholder="Ask about revenue, sources, definitions, or warehouse results…" rows={1} />
          </div>
        </div>
        <div className="composer-bottom">
          <button type="button" className="attach-button" onClick={() => { setMention(null); setPinOpen((open) => !open); }} aria-label="Add governed asset" title="Pin a governed asset (or type @ to reference inline)">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
          </button>
          <div className="run-mode" role="radiogroup" aria-label="Run mode">
            <span className="run-mode-label">Run mode</span>
            <button type="button" role="radio" aria-checked={governedOnly} className={`run-mode-option ${governedOnly ? "on" : ""}`} onClick={() => setGovernedOnly(true)} title="Answer only from governed, Git-authoritative context (default)">Governed only</button>
            <button type="button" role="radio" aria-checked={!governedOnly} className={`run-mode-option ${!governedOnly ? "on" : ""}`} onClick={() => setGovernedOnly(false)} title="Corroborate governed context with observed evidence from connected systems">Governed + observed</button>
          </div>
          {!admin && <div className="composer-controls"><AgentControls admin={false} embedded agent={agent} model={model} setAgent={setAgent} setModel={setModel} agents={agents} models={models} backendHealthy={backendHealthy} /></div>}
          {busy && <button className="cancel-button" type="button" onClick={onCancel} aria-label="Stop receiving this turn in your browser" title="Stop receiving this turn in your browser (context discovery already running keeps going on the server)"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2.5" /></svg></button>}
          <button className="send-button" type="button" onClick={submit} disabled={sendDisabled || !value.trim()} aria-label="Send question">↑</button>
        </div>
      </div>
      <div className="composer-hint"><span>Enter to send · Shift + Enter for a new line · @ to tag · + to pin</span><span>Read-only, governed, observable</span><span>Testing only · refresh clears chat</span></div>
    </div>
  );
}

function ChatActions({ onClear, disabled, backendHealthy }) {
  return <div className="chat-actions">
    <button className="clear-chat" type="button" onClick={onClear} disabled={disabled} title="Clear chat" aria-label="Clear chat"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3m-9 0 1 13h8l1-13M10 11v5m4-5v5" /></svg><span>Clear</span></button>
    <span className={`live-pill ${backendHealthy ? "connected" : "offline"}`}><i /> {backendHealthy ? "connected" : "not connected"}</span>
  </div>;
}

export function HypersetChat({ apiRoot = "/v0", admin = false, agent, model, models = [], agents = [], setAgent, setModel, backendHealthy = false, initialQuestion = "", initialGovernedOnly = true, initialMessages = [] }) {
  const restoredMessages = useMemo(() => redactDeep(Array.isArray(initialMessages) ? initialMessages : [])
    .filter((message) => (message?.role === "user" || message?.role === "assistant") && message.content)
    .map((message) => ({
      ...makeMessage(message.role, message.content),
      id: message.id || globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`,
      createdAt: message.createdAt || Date.now(),
      status: message.role === "assistant" ? "ready" : "sent",
      ...persistedAssistantEvidence(message),
    })), []);
  const [messages, setMessages] = useState(() => restoredMessages.length ? restoredMessages : [makeMessage("assistant", "Ask me a question and I’ll resolve the governed context, inspect its connections, and return an evidence-backed answer.")]);
  const scrollRef = useRef(null);
  const atBottomRef = useRef(true);
  const controllersRef = useRef(new Map()); // assistant message id -> AbortController
  const messagesRef = useRef(messages);
  const savedTurnsRef = useRef(new Set(restoredMessages.filter((message) => message.role === "user").map((message) => message.id)));
  useEffect(() => { messagesRef.current = messages; }, [messages]);
  useEffect(() => {
    const lastUserIndex = [...messages].map((message, index) => message.role === "user" ? index : -1).filter((index) => index >= 0).pop();
    if (lastUserIndex == null) return;
    const userMessage = messages[lastUserIndex];
    const answer = messages.slice(lastUserIndex + 1).find((message) => message.role === "assistant" && message.status === "ready" && message.content);
    if (!answer || savedTurnsRef.current.has(userMessage.id)) return;
    savedTurnsRef.current.add(userMessage.id);
    // Persist the turn WITH the run settings it was sent with (hy-87n1), so Recent threads
    // can restore the thread's state (agent / model / governed-only), not just re-prefill
    // the question. The settings were stamped on the user message at send time.
    const settings = userMessage.settings || {};
    saveThreadTurn(localStorage, {
      id: userMessage.id,
      question: userMessage.content,
      answer: answer.content,
      createdAt: userMessage.createdAt,
      agent: settings.agent,
      model: settings.model,
      governedOnly: settings.governedOnly,
      messages: messages.slice(messages.findIndex((message) => message.role === "user"))
        .filter((message) => message.role === "user" || (message.role === "assistant" && message.status === "ready" && message.content)),
    });
  }, [messages]);
  const client = useMemo(() => createHypersetClient({ apiRoot }), [apiRoot]);
  // A turn is in flight if any assistant message is queued or streaming. Sending
  // while busy is allowed: the server serializes turns and reports queue position.
  const busy = messages.some((message) => message.status === "queued" || message.status === "streaming");
  // Fade the scroll edge only when there is content past it (ChatGPT-style):
  // top fades when scrolled down, bottom fades when more is below. A permanent
  // mask would clip the very first message at rest, so it is class-gated.
  const updateFades = () => {
    const el = scrollRef.current;
    if (!el) return;
    el.classList.toggle("fade-top", el.scrollTop > 6);
    el.classList.toggle("fade-bottom", el.scrollHeight - el.scrollTop - el.clientHeight > 6);
  };
  const handleScroll = () => {
    const el = scrollRef.current;
    if (el) atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    updateFades();
  };
  useEffect(() => {
    // Only follow the stream when the user is already at the bottom; if they
    // scrolled up to read, don't yank them back down.
    if (scrollRef.current && atBottomRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    updateFades();
  }, [messages]);
  useEffect(() => {
    updateFades();
    window.addEventListener("resize", updateFades);
    return () => window.removeEventListener("resize", updateFades);
  }, []);

  const clearChat = () => { if (!busy) setMessages([]); };
  const updateAssistant = (id, updater) => setMessages((current) => current.map((message) => message.id === id ? updater(message) : message));
  // Cancel the running turn only; queued follow-ups keep their place and proceed.
  const cancelRunning = () => {
    const target = messagesRef.current.find((message) => message.status === "streaming")
      || messagesRef.current.find((message) => message.status === "queued");
    if (target) controllersRef.current.get(target.id)?.abort();
  };
  const send = async (question, { governedOnly = false, attachments = [] } = {}) => {
    // History is captured now (completed turns only) since the request body is
    // fixed at send time; a queued follow-up won't see an answer still streaming.
    const prior = messagesRef.current
      .filter((message) => message.role === "user" || (message.role === "assistant" && message.status === "ready" && message.content))
      .map((message) => ({ role: message.role, content: message.content }));
    const userMessage = makeMessage("user", question);
    userMessage.attachments = attachments;
    // Stamp the run settings this turn is sent with, so the saved thread can restore them.
    userMessage.settings = { agent, model, governedOnly };
    const assistant = makeMessage("assistant");
    assistant.status = "queued";
    const controller = new AbortController();
    controllersRef.current.set(assistant.id, controller);
    atBottomRef.current = true; // a fresh turn always scrolls into view
    setMessages((current) => [...current, userMessage, assistant]);
    try {
      const selected = models.find((item) => item.value === model);
      if (!selected) throw new Error("The backend has not published a model configuration.");
      const response = await client.streamChat({ question, history: prior, agent, model: selected.value, provider: selected.provider, governed_only: governedOnly, attachments }, { signal: controller.signal });
      if (!response.ok || !response.body) throw new Error((await response.text()) || `request failed (${response.status})`);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      const consume = (chunk) => {
        buffer += chunk;
        const packets = buffer.split("\n\n");
        buffer = packets.pop() || "";
        for (const packet of packets) {
          const line = packet.split("\n").find((item) => item.startsWith("data: "));
          if (!line) continue;
          const event = JSON.parse(line.slice(6));
          if (event.type === "queued") updateAssistant(assistant.id, (message) => ({ ...message, status: "queued", queuePosition: event.position }));
          if (event.type === "start") updateAssistant(assistant.id, (message) => ({ ...message, status: "streaming", queuePosition: undefined }));
          if (event.type === "stage") updateAssistant(assistant.id, (message) => ({ ...message, status: "streaming", stages: [...message.stages.filter((item) => item.stage !== event.stage), event] }));
          if (event.type === "selection") updateAssistant(assistant.id, (message) => ({ ...message, selection: event.selection }));
          if (event.type === "bundle") updateAssistant(assistant.id, (message) => ({ ...message, bundle: event.bundle }));
          if (event.type === "resolution_error") updateAssistant(assistant.id, (message) => ({ ...message, resolutionError: event.error }));
          if (event.type === "sql") updateAssistant(assistant.id, (message) => ({ ...message, sql: event.result }));
          if (event.type === "token") updateAssistant(assistant.id, (message) => ({ ...message, content: message.content + event.delta }));
          if (event.type === "done") updateAssistant(assistant.id, (message) => ({ ...message, result: event.result, content: event.result.answer || message.content, status: "ready", finishedAt: Date.now(), blocked: event.result.governed_blocked ? { question, attachments } : null }));
          if (event.type === "error") updateAssistant(assistant.id, (message) => ({ ...message, status: "error", error: event.error }));
        }
      };
      while (true) { const { value: chunk, done } = await reader.read(); if (done) break; consume(decoder.decode(chunk, { stream: true })); }
      consume(decoder.decode());
    } catch (error) {
      if (error.name === "AbortError") updateAssistant(assistant.id, (message) => ({ ...message, status: "cancelled", finishedAt: Date.now() }));
      else updateAssistant(assistant.id, (message) => ({ ...message, status: "error", error: error.message }));
    } finally { controllersRef.current.delete(assistant.id); }
  };

  return (
    <section className={`hyperset-chat-ui chat-shell ${admin ? "admin-chat" : "user-chat"}`}>
      {admin ? <div className="chat-heading"><div><span className="eyebrow">Live operator trace</span><h1>See the agent resolve the whole picture.</h1></div><ChatActions onClear={clearChat} disabled={busy || messages.length === 0} backendHealthy={backendHealthy} /></div> : <div className="chat-toolbar"><ChatActions onClear={clearChat} disabled={busy || messages.length === 0} backendHealthy={backendHealthy} /></div>}
      <div className="messages" ref={scrollRef} onScroll={handleScroll}>{messages.length ? messages.map((message) => <Message key={message.id} message={message} admin={admin} onContinue={(blocked) => send(blocked.question, { governedOnly: false, attachments: blocked.attachments })} continueDisabled={busy} />) : <div className="empty-chat"><strong>Chat cleared.</strong><small>Start a new question when you’re ready.</small></div>}</div>
      <Composer onSend={send} sendDisabled={!backendHealthy || !agent || !model} busy={busy} onCancel={cancelRunning} admin={admin} agent={agent} model={model} agents={agents} models={models} setAgent={setAgent} setModel={setModel} backendHealthy={backendHealthy} apiRoot={apiRoot} initialQuestion={initialQuestion} initialGovernedOnly={initialGovernedOnly} />
    </section>
  );
}
