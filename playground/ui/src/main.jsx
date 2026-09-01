import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, useLocation, useNavigate } from "react-router-dom";
import { HypersetChat, Markdown, readThreadRestore, redactDeep, writeThreadRestore } from "@hyperset/chat-ui";
import { ApiConsole } from "./console.jsx";
import { DiagnosticsPanel } from "./diagnostics.jsx";
import { HiveMindGraph } from "./hive_mind.jsx";
import { WritebackTargetsPanel } from "./writeback_targets.jsx";
import { ContextSourcesPanel } from "./context_sources.jsx";
import { ConnectionsPanel } from "./connections.jsx";
import { ReviewRouting } from "./review_routing.jsx";
import "./styles.css";

function isAdmin() {
  const path = window.location.pathname;
  return path === "/admin" || path.startsWith("/admin/");
}

// The reviewer surface is its own first-class route (hy-1f96), not a playground
// tab. It is public -- anyone reviews (hy-529x) -- so it uses the public api
// prefix; only the admin SETTINGS surface uses the admin prefix.
function isReview() {
  const path = window.location.pathname;
  return path === "/review" || path.startsWith("/review/");
}

// One page is one surface. The admin SETTINGS surface uses the admin prefix, so
// the write-back target write lands on the admin surface the backend gates and
// is refused on the public one (hy-529x). The public playground and the review
// surface share the public prefix.
const API_ROOT = isAdmin() ? "/admin/api" : "/playground/api";

// Product navigation is intentionally small. The old home/docs/help/profile/
// threads routes remain as importable components for regression coverage, but
// they are archived from the product shell and canonicalize back to Live chat.
const USER_PAGE_KEYS = new Set(["explore"]);

function userPageFromPath(pathname, basePath) {
  const segment = pathname.slice(basePath.length).split("/").filter(Boolean)[0] || "";
  return USER_PAGE_KEYS.has(segment) ? segment : "";
}

function SurfaceNav({ current, userShell = false }) {
  // Cross-surface links are plain anchors, not client-side routes: each surface
  // resolves its api prefix from window.location at load, so switching surfaces
  // is a full navigation, not a router push (hy-1f96).
  const links = userShell ? [
    ["/playground/", "Live chat", "chat"],
    ["/playground/explore/", "Explore the Hive-Mind", "explore"],
    ["separator", "", "separator"],
    ["/review/", "Review", "review"],
    ["/admin/", "Settings", "admin"],
  ] : [
    ["/playground/", "Playground", "playground"],
    ["/review/", "Review", "review"],
    ["/admin/", "Settings", "admin"],
  ];
  return <nav className={`surface-nav ${userShell ? "user-shell-nav" : ""}`} aria-label={userShell ? "Hyperset workspace" : "Hyperset surfaces"}>
    {links.map(([href, label, key]) => key === "separator"
      ? <span key={key} className="surface-divider" aria-hidden="true" />
      : <a key={key} href={href} className={key === current ? "surface-link active" : "surface-link"} aria-current={key === current ? "page" : undefined}>{label}</a>)}
  </nav>;
}

function Header({ surface, theme, onThemeChange, userShell = false, userSection = "chat", auth = {} }) {
  const authEnabled = Boolean(auth.enabled);
  return (
    <header className="site-header">
      <a className="brand" href="/" aria-label="Go to Hyperset home">
        <span className="brand-mark">h</span>
        <span>hyperset</span>
      </a>
      <div className="public-header-nav">
        <SurfaceNav current={userShell ? userSection : surface} userShell={userShell} />
      </div>
      <div className="header-actions">
        {/* F4 login/logout (hy-jyha). The deep link back to the current page is preserved
            through login. When authz is off (loopback dev) these are inert -- /login just
            returns here -- so they are always present but harmless. */}
        {authEnabled
          ? <><a className="auth-link" href={`/login?return=${encodeURIComponent(window.location.pathname)}`}>Log in</a><a className="auth-link" href="/logout?return=/">Log out</a></>
          : <span className="auth-state" title="OIDC is disabled for this loopback demo">Local demo · auth off</span>}
        <button className="theme-toggle" type="button" onClick={() => onThemeChange(theme === "dark" ? "light" : "dark")} aria-label="Toggle theme">
          {theme === "dark" ? "☼" : "☾"}
        </button>
      </div>
    </header>
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

function SqlResult({ sql }) {
  if (!sql) return null;
  const rows = sql.rows || [];
  const columns = sql.columns || (rows[0] ? Object.keys(rows[0]) : []);
  return (
    <details className="sql-card" open>
      <summary><span><i className="summary-dot" />SQL used</span><small>{sql.error ? "query failed" : `${sql.row_count ?? rows.length} rows returned`}</small></summary>
      <pre><code>{sql.sql || "-- no query"}</code></pre>
      {sql.error ? <p className="error-text">{sql.error}</p> : rows.length > 0 && (
        <div className="result-table-wrap"><table><thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{rows.slice(0, 8).map((row, index) => <tr key={index}>{columns.map((column) => <td key={column}>{String(row[column] ?? "—")}</td>)}</tr>)}</tbody></table></div>
      )}
    </details>
  );
}

// The internal diagnostics are intentionally retained for unit coverage and
// local debugging, but are no longer product routes. The MVP exposes the
// tested live chat and the dedicated Hive-Mind explorer instead of making
// operators hunt through implementation views.
const ARCHIVED_DEBUG_TABS = [
  ["mcp", "MCP setup"],
  ["environment", "Environment"],
  ["catalog", "Catalog"],
  ["discover", "Discover candidates"],
  ["bundle", "Bundle resolver"],
  ["validation", "Plan validation"],
  ["console", "API console"],
  ["builder", "Agent Builder"],
  ["harness", "Agent Evaluator"],
  ["graph", "Domain graph"],
];
const DEBUG_TABS = [["chat", "Live chat"]];
const DEBUG_TAB_KEYS = new Set(DEBUG_TABS.map(([key]) => key));

function tabPath(basePath, tab) {
  return tab === "chat" ? `${basePath}/` : `${basePath}/${tab}/`;
}

function tabFromPath(pathname, basePath) {
  const pathSegment = pathname.slice(basePath.length).split("/").filter(Boolean)[0] || "chat";
  return DEBUG_TAB_KEYS.has(pathSegment) ? pathSegment : "chat";
}

function adminTabPath() {
  return "/admin/";
}

// Settings is deliberately one admin surface. Legacy admin subpaths remain
// importable for regression coverage, but every old bookmark canonicalizes to
// the single Settings page instead of exposing archived tab routes.
function adminTabFromPath() {
  return "readiness";
}

async function requestJson(path, payload, method = "POST") {
  const response = await fetch(`${API_ROOT}${path}`, {
    method,
    headers: payload ? { "Content-Type": "application/json" } : undefined,
    body: payload ? JSON.stringify(payload) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.error === "string" ? data.error : data.error?.message || `HTTP ${response.status}`);
  return data;
}

function DebugJson({ value, empty = "Nothing loaded yet." }) {
  return <pre className="debug-json">{value ? JSON.stringify(value, null, 2) : empty}</pre>;
}

// A fetch that never throws, so the MCP wizard can CLASSIFY the failure (transport
// unreachable vs auth rejected vs a 404) instead of collapsing every one into a
// generic error string the way `requestJson` does. Returns the status and parsed
// body so the caller can branch on the real `error.code` the backend delivers.
async function probeJson(path, payload, method = "POST", extraHeaders = {}) {
  let response;
  const headers = { ...(payload ? { "Content-Type": "application/json" } : {}), ...extraHeaders };
  try {
    response = await fetch(`${API_ROOT}${path}`, {
      method,
      headers,
      body: payload ? JSON.stringify(payload) : undefined,
    });
  } catch (networkError) {
    return { ok: false, status: 0, data: null, networkError: networkError.message || "network error" };
  }
  const data = await response.json().catch(() => ({}));
  return { ok: response.ok, status: response.status, data, networkError: null };
}

function parseMcpResponse(body) {
  const lines = String(body || "").split("\n").filter((line) => line.startsWith("data:"));
  const candidate = lines.length ? lines[lines.length - 1].slice(5).trim() : String(body || "").trim();
  try { return candidate ? JSON.parse(candidate) : {}; } catch { return {}; }
}

export async function probeMcp(endpoint, payload, extraHeaders = {}) {
  let response;
  try {
    response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json, text/event-stream", ...extraHeaders },
      body: JSON.stringify(payload),
    });
  } catch (networkError) {
    return { ok: false, status: 0, data: null, networkError: networkError.message || "network error", sessionId: null };
  }
  const data = parseMcpResponse(await response.text());
  return {
    ok: response.ok && !data.error && !!data.result,
    status: response.status,
    data,
    networkError: null,
    sessionId: response.headers.get("mcp-session-id"),
  };
}

function mcpToolPayload(response) {
  const result = response.data?.result || {};
  const structured = result.structuredContent;
  if (structured && typeof structured === "object") return structured;
  const text = result.content?.find((entry) => entry.type === "text")?.text;
  try { return text ? JSON.parse(text) : {}; } catch { return {}; }
}

async function probeMcpTool(endpoint, name, argumentsValue, id, headers) {
  const response = await probeMcp(endpoint, {
    jsonrpc: "2.0",
    id,
    method: "tools/call",
    params: { name, arguments: argumentsValue },
  }, headers);
  const data = mcpToolPayload(response);
  return { ...response, ok: response.ok && !response.data?.result?.isError && !data.error, data };
}

function classifyMcpFailure(probe, endpoint) {
  if (probe.networkError) return { state: "failed", detail: `Transport unreachable — ${probe.networkError}` };
  const code = probe.data?.error?.code;
  if (probe.status === 401 || code === "unauthorized") {
    return { state: "failed", detail: `Auth rejected — ${probe.data?.error?.message || "present a valid bearer token"}` };
  }
  if (probe.status === 404 || code === "unknown_route") return { state: "failed", detail: `Endpoint not found (404) — ${endpoint}` };
  return { state: "failed", detail: `MCP error (HTTP ${probe.status || "?"}) — ${probe.data?.error?.message || "the endpoint did not complete the MCP handshake"}` };
}

// A failed probe, mapped to one classified outcome. The wizard shows a DISTINCT
// state, not a generic error: `error.code` is the backend's own vocabulary
// (unauthorized / unknown_route / internal_error, transport/operations.py), and a
// rejected fetch is the only "unreachable" signal a browser gets.
function classifyProbeFailure(probe, path) {
  // Returns the step's {state, detail}; the classified NAME leads the detail so it
  // renders (McpStep shows state + detail, not a separate label).
  if (probe.networkError) {
    return { state: "failed", detail: `Transport unreachable — ${probe.networkError}` };
  }
  const code = probe.data?.error?.code;
  if (probe.status === 401 || code === "unauthorized") {
    const recovery = probe.data?.error?.recovery || "present a valid bearer token for an authorized identity";
    return { state: "failed", detail: `Auth rejected — ${recovery}` };
  }
  if (probe.status === 404 || code === "unknown_route") {
    return { state: "failed", detail: `Endpoint not found (404) — ${API_ROOT}${path}` };
  }
  const message = probe.data?.error?.message || "the server did not return a governed response";
  return { state: "failed", detail: `Server error (HTTP ${probe.status}) — ${message}` };
}

// resolution.status -> how the wizard names it. The four are the resolver's own
// (bundle.schema RESOLUTION_STATUSES); abstention (no_match) is a VALID answer,
// not a failure, so it is a distinct blue state rather than a red one.
const MCP_RESOLVE_STATES = {
  governed: { state: "ready", label: "Context ready" },
  mixed: { state: "warn", label: "Mixed — partial governed context" },
  observed_only: { state: "warn", label: "Observed-only — no governed authority yet" },
  no_match: { state: "abstain", label: "Abstained — Git governs nothing for this question" },
};

const MCP_DEFAULT_ENDPOINT = "http://localhost:8010/mcp";
const MCP_DEFAULT_QUESTION = "Which source and rules should an analyst use for recognized revenue by region?";
const MCP_DOCS_URL = "/admin/#mcp";

// The copyable client config, constructed from the known MCP constants (there is
// no server endpoint that emits it). HTTP carries the endpoint (and a bearer
// header only when the operator entered one); stdio is a local trusted subprocess
// launched by `hyperset serve mcp`, so it takes a command, not a URL or a token.
function mcpClientConfig({ transport, endpoint, authToken }) {
  if (transport === "stdio") {
    return { mcpServers: { hyperset: { command: "hyperset", args: ["serve", "mcp"] } } };
  }
  const server = { url: endpoint };
  if (authToken.trim()) server.headers = { Authorization: `Bearer ${authToken.trim()}` };
  return { mcpServers: { hyperset: server } };
}

function McpStep({ step }) {
  const labels = { ready: "Passed", warn: "Attention", failed: "Failed", abstain: "Abstained", running: "Running…", pending: "Waiting" };
  return (
    <div className="mcp-step">
      <div className="mcp-step-copy">
        <strong>{step.title}</strong>
        <span>{step.detail || "—"}</span>
      </div>
      <span className={`mcp-status mcp-status-${step.state}`}>{labels[step.state] || step.state}</span>
    </div>
  );
}

// The MCP onboarding wizard (hy-8u0a, V1 gap E). Configure a client, copy the
// config, and run a BOUNDED handshake against THIS deployment's governed
// operations -- the same discover -> resolve -> validate the MCP tools expose --
// classifying each outcome. It resolves no meaning and writes nothing; the
// authority to govern stays in Git (ADR 0012).
export function McpSetupWizard() {
  const [client, setClient] = useState("Claude Desktop");
  const [transport, setTransport] = useState("http");
  const [endpoint, setEndpoint] = useState(MCP_DEFAULT_ENDPOINT);
  const [authToken, setAuthToken] = useState("");
  const [question, setQuestion] = useState(MCP_DEFAULT_QUESTION);
  const [steps, setSteps] = useState(null);
  const [testing, setTesting] = useState(false);
  const [copied, setCopied] = useState(false);

  const config = mcpClientConfig({ transport, endpoint, authToken });
  const configText = JSON.stringify(config, null, 2);

  const copyConfig = async () => {
    try {
      await navigator.clipboard.writeText(configText);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };

  const runTest = async () => {
    setTesting(true);
    // 1. Reachability + the served tool list. HTTP tests the entered MCP endpoint itself;
    // stdio cannot be spawned from this browser, so it checks the local API contract.
    let stepList = [
      { key: "reachable", title: "Endpoint reachable", state: "running", detail: "" },
      { key: "tools", title: "Tools discovered", state: "pending", detail: "" },
      { key: "discover", title: "Discovery ranked the catalog", state: "pending", detail: "" },
      { key: "resolve", title: "Context resolved", state: "pending", detail: "" },
    ];
    setSteps(stepList);
    const set = (key, patch) => {
      stepList = stepList.map((step) => (step.key === key ? { ...step, ...patch } : step));
      setSteps(stepList);
    };

    // The bearer the operator entered for the config is also sent on the test
    // probes, so "Auth rejected" is a real outcome when authz is enabled rather
    // than a state the wizard can never reach. stdio carries no token.
    const authHeaders =
      transport === "http" && authToken.trim() ? { Authorization: `Bearer ${authToken.trim()}` } : {};

    let operations;
    let mcpRequestId = 2;
    let invoke;
    if (transport === "http") {
      const initialized = await probeMcp(endpoint, {
        jsonrpc: "2.0",
        id: 1,
        method: "initialize",
        params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "hyperset-settings-smoke", version: "0.1" } },
      }, authHeaders);
      if (!initialized.ok) {
        set("reachable", classifyMcpFailure(initialized, endpoint));
        setTesting(false);
        return;
      }
      const mcpHeaders = { "MCP-Protocol-Version": "2025-06-18", ...authHeaders, ...(initialized.sessionId ? { "Mcp-Session-Id": initialized.sessionId } : {}) };
      const listed = await probeMcp(endpoint, { jsonrpc: "2.0", id: 2, method: "tools/list", params: {} }, mcpHeaders);
      if (!listed.ok) {
        set("reachable", { state: "ready", detail: `MCP initialize HTTP ${initialized.status}` });
        set("tools", classifyMcpFailure(listed, endpoint));
        setTesting(false);
        return;
      }
      operations = (listed.data?.result?.tools || []).map((tool) => tool.name).filter(Boolean);
      set("reachable", { state: "ready", detail: `MCP initialize HTTP ${initialized.status} · ${initialized.data?.result?.protocolVersion || "negotiated"}` });
      invoke = (name, args) => probeMcpTool(endpoint, name, args, ++mcpRequestId, mcpHeaders);
    } else {
      const health = await probeJson("/v0/health", null, "GET", authHeaders);
      if (!health.ok) {
        set("reachable", classifyProbeFailure(health, "/v0/health"));
        setTesting(false);
        return;
      }
      operations = health.data?.operations || [];
      set("reachable", { state: "ready", detail: `Local API HTTP 200 · schema ${health.data?.schema_version ?? "?"}` });
      invoke = (name, args) => probeJson(`/v0/${name}`, args, "POST", authHeaders);
    }

    // 2. The MCP tools are exactly the served operations; name the three the
    // wizard drives so a missing surface is visible rather than a later 404.
    const expected = ["discover_analytics_context", "resolve_analytics_context", "validate_analytics_plan"];
    const missing = expected.filter((name) => !operations.includes(name));
    if (missing.length) {
      set("tools", { state: "failed", detail: `missing operation(s): ${missing.join(", ")}` });
      setTesting(false);
      return;
    }
    set("tools", { state: "ready", detail: `${operations.length} tools · discover · resolve · validate` });

    // 3. First real discover call. An empty ranking is a classified state (no
    // candidate to resolve), not a crash.
    set("discover", { state: "running", detail: "" });
    const discovery = await invoke("discover_analytics_context", { query: question });
    if (!discovery.ok) {
      set("discover", classifyProbeFailure(discovery, "/v0/discover_analytics_context"));
      setTesting(false);
      return;
    }
    const candidates = discovery.data?.candidates || [];
    if (!candidates.length) {
      set("discover", { state: "warn", detail: "Discovery returned no candidates for this question — pick another or sync a domain." });
      set("resolve", { state: "pending", detail: "Skipped: no candidate to resolve." });
      setTesting(false);
      return;
    }
    // Build a directive the resolver will ACCEPT: a named domain must come WITH the
    // concept terms it declares -- resolve rejects {domains:[d], concepts:[]} as
    // invalid_params (hy-xz83). Take the concept terms discovery ranked for the chosen
    // domain; prefer a domain that also has concept candidates, else fall back to the
    // top concept candidate's own domain (which by construction carries one).
    const conceptCandidates = candidates.filter(
      (entry) => entry.kind === "concept" && entry.domain && entry.term,
    );
    const domainCandidate = candidates.find((entry) => entry.kind === "domain" && entry.domain);
    let domain = domainCandidate?.domain;
    let concepts = conceptCandidates.filter((entry) => entry.domain === domain).map((entry) => entry.term);
    if (!concepts.length && conceptCandidates.length) {
      domain = conceptCandidates[0].domain;
      concepts = conceptCandidates.filter((entry) => entry.domain === domain).map((entry) => entry.term);
    }
    concepts = [...new Set(concepts)];
    if (!domain || !concepts.length) {
      // Discovery surfaced a domain but no concept term to pair with it, so no directive
      // the resolver accepts can be built from this ranking -- say so rather than send an
      // invalid one that always 400s.
      set("discover", { state: "warn", detail: `${candidates.length} candidate(s), but none pair a domain with a concept term to resolve.` });
      set("resolve", { state: "pending", detail: "Skipped: the resolver needs a domain named with its concepts." });
      setTesting(false);
      return;
    }
    set("discover", { state: "ready", detail: `${candidates.length} candidate(s) · resolving ${domain} · ${concepts.join(", ")}` });

    // 4. First real resolve call, against the discovered domain AND its concept terms.
    // Classify the resolution's own status (governed / mixed / observed-only / abstained).
    set("resolve", { state: "running", detail: "" });
    const directive = { domains: [domain], concepts };
    const resolved = await invoke("resolve_analytics_context", { query: question, directive });
    if (!resolved.ok) {
      set("resolve", classifyProbeFailure(resolved, "/v0/resolve_analytics_context"));
      setTesting(false);
      return;
    }
    const status = resolved.data?.resolution?.status;
    const mapped = MCP_RESOLVE_STATES[status] || { state: "warn", label: `Unrecognized status: ${status}` };
    const warnings = (resolved.data?.resolution?.warnings || []).map((warning) => warning.code);
    const conflicts = (resolved.data?.linked_evidence?.conflicts || []).length;
    const notes = [
      `${mapped.label}`,
      resolved.data?.bundle_id,
      warnings.length ? `warnings: ${warnings.join(", ")}` : null,
      conflicts ? `${conflicts} conflict(s)` : null,
    ].filter(Boolean);
    set("resolve", { state: mapped.state, detail: notes.join(" · ") });
    setTesting(false);
  };

  return (
    <div className="mcp-wizard">
      <p className="debug-lede">
        Connect an MCP agent to this Hyperset deployment: configure a client, copy the config, then run a bounded
        handshake against the same governed operations the MCP tools expose (discover → resolve → validate). It
        resolves no meaning and writes nothing — Git stays the serving authority.
      </p>

      <div className="mcp-grid">
        <section className="mcp-card">
          <h3>1. Configure your client</h3>
          <label className="mcp-field"><span>MCP client</span>
            <select value={client} onChange={(event) => setClient(event.target.value)}>
              <option>Claude Desktop</option>
              <option>Cursor</option>
              <option>Custom MCP client</option>
            </select>
          </label>
          <label className="mcp-field"><span>Transport</span>
            <select value={transport} onChange={(event) => setTransport(event.target.value)}>
              <option value="http">Streamable HTTP (recommended)</option>
              <option value="stdio">stdio (local subprocess)</option>
            </select>
          </label>
          {transport === "http" ? (
            <>
              <label className="mcp-field"><span>Endpoint</span>
                <input type="url" value={endpoint} onChange={(event) => setEndpoint(event.target.value)} />
              </label>
              <label className="mcp-field"><span>Bearer token <small>(only if authz is enabled)</small></span>
                <input type="password" value={authToken} placeholder="optional" onChange={(event) => setAuthToken(event.target.value)} />
              </label>
            </>
          ) : (
            <p className="mcp-note">stdio launches Hyperset as a trusted local subprocess (<code>hyperset serve mcp</code>); the OS process is the identity, so no endpoint or token is sent.</p>
          )}
        </section>

        <section className="mcp-card mcp-card-primary">
          <div className="mcp-card-head"><h3>2. Copy config</h3>
            <button type="button" className="debug-button" onClick={copyConfig}>{copied ? "Copied" : "Copy"}</button>
          </div>
          <pre className="mcp-code"><code>{configText}</code></pre>
          <p className="mcp-note">Paste this into {client}&rsquo;s MCP server settings.</p>
        </section>
      </div>

      <section className="mcp-card">
        <div className="mcp-card-head">
          <div><h3>3. Test connection</h3>
            <p className="mcp-note">A bounded discover → resolve round-trip before you ask a real question.</p>
          </div>
          <button type="button" className="debug-button primary" disabled={testing} onClick={runTest}>{testing ? "Testing…" : "Run test"}</button>
        </div>
        <label className="mcp-field"><span>Question</span>
          <input value={question} onChange={(event) => setQuestion(event.target.value)} />
        </label>
        {steps ? <div className="mcp-steps">{steps.map((step) => <McpStep key={step.key} step={step} />)}</div>
          : <div className="empty-debug">Run the test to see a classified handshake.</div>}
      </section>

      <details className="mcp-help"><summary>Need help?</summary>
        <p className="mcp-note">Read the <a href={MCP_DOCS_URL} target="_blank" rel="noreferrer">HTTP/MCP guide</a> for response anatomy, auth, safe abstention, and recovery when a test fails.</p>
      </details>
    </div>
  );
}

function StatusGrid({ data }) {
  const services = data ? [data.hyperset, data.superset, data.datahub, data.openai, { name: "Analytics DB", status: data.analytics_db?.status, version: data.analytics_db?.database }] : [];
  return <div className="status-grid">{services.map((service, index) => <article className="status-card" key={`${service?.name || "service"}-${index}`}><div><b>{service?.name || "Service"}</b><span className={`status-badge ${service?.status || "unknown"}`}>{service?.status || "unknown"}</span></div><small>{service?.version || service?.endpoint || "No metadata returned"}</small>{service?.detail && <p>{service.detail}</p>}</article>)}</div>;
}

function DomainPicker({ catalog, selectedDomain, selectedConcept, onDomainChange, onConceptChange }) {
  const selectedEntry = catalog?.domains?.find((entry) => entry.domain === selectedDomain);
  const concepts = selectedEntry?.concepts || [];
  return <div className="debug-form domain-picker">
    <label>Domain<select value={selectedDomain} onChange={(event) => onDomainChange(event.target.value)}><option value="">Choose a synced domain</option>{(catalog?.domains || []).map((entry) => <option key={entry.domain} value={entry.domain}>{entry.domain}</option>)}</select></label>
    <label>Concept<select value={selectedConcept} onChange={(event) => onConceptChange(event.target.value)}><option value="">All concepts in domain</option>{concepts.map((concept) => <option key={concept} value={concept}>{concept}</option>)}</select></label>
  </div>;
}

// The DISTINCT, human-legible label for a graph node. The raw ref shares a long common
// prefix across sources (table:postgres:analytics:<table>), so a front-truncation collapses
// every source card to the same "table:postgres:analytics" (hy-ha1jv). Show the most-specific
// trailing segment instead, which is what actually differs; the full ref stays in the title
// and the selection detail. When two nodes reduce to the same tail, keep the parent segment
// too so they remain distinguishable.
function graphNodeLabel(node, disambiguate = false) {
  const raw = String(node?.label || node?.id || "");
  const parts = raw.split(":").filter(Boolean);
  if (parts.length <= 1) return raw;
  return disambiguate ? parts.slice(-2).join(":") : parts[parts.length - 1];
}

function graphKindClass(kind) {
  return `graph-kind-${String(kind || "node").replace(/[^a-z0-9]+/gi, "-").toLowerCase()}`;
}

export function DomainGraphView({ bundle }) {
  const graph = bundle?.domain_graph || {};
  const nodes = Array.isArray(graph.nodes) ? graph.nodes : [];
  const edges = Array.isArray(graph.edges) ? graph.edges : [];
  // Navigation affordance (hy-ha1jv): click a node to select it. The selected node and the
  // edges touching it are highlighted and the rest is dimmed, so a dense 42-node fan-out can
  // be followed one node at a time. Keyboard-operable (Enter/Space) and screen-reader-labelled.
  const [selected, setSelected] = useState(null);
  if (!nodes.length) return <div className="empty-debug">No domain graph was returned for this selection.</div>;
  const columns = Math.min(4, Math.max(1, nodes.length));
  const nodeWidth = 200;
  const nodeHeight = 48;
  const horizontalGap = 34;
  const verticalGap = 38;
  const width = Math.max(760, columns * (nodeWidth + horizontalGap));
  const rows = Math.ceil(nodes.length / columns);
  const height = Math.max(210, rows * (nodeHeight + verticalGap) + 42);
  const positions = new Map(nodes.map((node, index) => [node.id, {
    x: 18 + (index % columns) * (nodeWidth + horizontalGap),
    y: 20 + Math.floor(index / columns) * (nodeHeight + verticalGap),
  }]));
  // A tail that repeats across nodes keeps its parent segment so the labels stay distinct.
  const tailCounts = nodes.reduce((acc, node) => { const t = graphNodeLabel(node); acc[t] = (acc[t] || 0) + 1; return acc; }, {});
  const displayLabel = (node) => graphNodeLabel(node, tailCounts[graphNodeLabel(node)] > 1);
  const neighbours = selected ? new Set(edges.filter((e) => e.from === selected || e.to === selected).flatMap((e) => [e.from, e.to])) : null;
  const nodeKinds = [...new Set(nodes.map((n) => n.kind || "node"))];
  const edgeKinds = [...new Set(edges.map((e) => e.relation || "related"))];
  const selectedNode = selected ? nodes.find((n) => n.id === selected) : null;
  const selectedDegree = selected ? edges.filter((e) => e.from === selected || e.to === selected).length : 0;
  const toggle = (id) => setSelected((current) => (current === id ? null : id));
  return <div className="domain-graph-visual">
    <div className="graph-legend" aria-label="Graph legend">
      <span className="graph-legend-title">Legend</span>
      {nodeKinds.map((kind) => <span key={`node-${kind}`} className={`graph-legend-item node ${graphKindClass(kind)}`}><span className="graph-legend-swatch" aria-hidden="true" />{kind}</span>)}
      {edgeKinds.map((kind) => <span key={`edge-${kind}`} className="graph-legend-item edge"><span className="graph-legend-edge" aria-hidden="true" />{kind}</span>)}
    </div>
    <svg className={`domain-graph-svg${selected ? " has-selection" : ""}`} viewBox={`0 0 ${width} ${height}`} role="group" aria-label={`Domain graph with ${nodes.length} nodes and ${edges.length} relationships`}>
      <defs><marker id="domain-graph-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 z" fill="currentColor" /></marker></defs>
      {edges.map((edge, index) => {
        const from = positions.get(edge.from);
        const to = positions.get(edge.to);
        if (!from || !to) return null;
        const x1 = from.x + nodeWidth / 2;
        const y1 = from.y + nodeHeight / 2;
        const x2 = to.x + nodeWidth / 2;
        const y2 = to.y + nodeHeight / 2;
        const active = selected && (edge.from === selected || edge.to === selected);
        const dim = selected && !active;
        return <g key={`${edge.from}-${edge.to}-${index}`} className={`domain-graph-edge${active ? " active" : ""}${dim ? " dim" : ""}`}><line x1={x1} y1={y1} x2={x2} y2={y2} markerEnd="url(#domain-graph-arrow)" /><title>{`${edge.relation || "related"}: ${edge.from} → ${edge.to}`}</title></g>;
      })}
      {nodes.map((node) => {
        const position = positions.get(node.id);
        const isSelected = selected === node.id;
        const isNeighbour = neighbours ? neighbours.has(node.id) : false;
        const dim = selected && !isSelected && !isNeighbour;
        const cls = `domain-graph-node ${graphKindClass(node.kind)}${isSelected ? " selected" : ""}${isNeighbour && !isSelected ? " neighbour" : ""}${dim ? " dim" : ""}`;
        return <g key={node.id} className={cls} role="button" tabIndex={0} aria-pressed={isSelected} aria-label={`${node.kind || "node"}: ${node.label || node.id}`} transform={`translate(${position.x}, ${position.y})`} onClick={() => toggle(node.id)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(node.id); } }}>
          <rect width={nodeWidth} height={nodeHeight} rx="7" />
          <text x="10" y="20" className="domain-graph-node-kind">{node.kind || "node"}</text>
          <text x="10" y="36" className="domain-graph-node-label">{displayLabel(node)}</text>
          <title>{node.label || node.id}</title>
        </g>;
      })}
    </svg>
    <div className="graph-detail" aria-live="polite">
      {selectedNode
        ? <><span className="graph-detail-kind">{selectedNode.kind || "node"}</span><b className="graph-detail-label">{selectedNode.label || selectedNode.id}</b><small>{selectedDegree} relationship{selectedDegree === 1 ? "" : "s"}</small><button type="button" className="linklike" onClick={() => setSelected(null)}>Clear selection</button></>
        : <small>Click a node to follow its relationships; the rest dims so a dense graph stays legible.</small>}
    </div>
  </div>;
}

function PolicyTable({ title, options, draft, setDraft, allowKey, denyKey, favorKey }) {
  const update = (key, value, checked) => setDraft((current) => {
    const values = new Set(current[key] || []);
    if (checked) values.add(value); else values.delete(value);
    const next = { ...current, [key]: [...values].sort() };
    if (checked && key === allowKey) next[denyKey] = (current[denyKey] || []).filter((item) => item !== value);
    if (checked && key === denyKey) next[allowKey] = (current[allowKey] || []).filter((item) => item !== value);
    return next;
  });
  if (!options.length) return null;
  return <div className="policy-table-wrap">
    <div className="policy-table-title">{title}</div>
    <div className={`policy-table ${favorKey ? "with-favor" : ""}`}>
      <div className="policy-table-row policy-table-head"><span>Option</span><span>Allow</span><span>Disallow</span>{favorKey && <span>Favor</span>}</div>
      {options.map((option) => <div className="policy-table-row" key={option.value}>
        <b title={option.value}>{option.label}</b>
        <input type="checkbox" aria-label={`Allow ${option.label}`} checked={(draft[allowKey] || []).includes(option.value)} onChange={(event) => update(allowKey, option.value, event.target.checked)} />
        <input type="checkbox" aria-label={`Disallow ${option.label}`} checked={(draft[denyKey] || []).includes(option.value)} onChange={(event) => update(denyKey, option.value, event.target.checked)} />
        {favorKey && <input type="checkbox" aria-label={`Favor ${option.label}`} checked={(draft[favorKey] || []).includes(option.value)} onChange={(event) => update(favorKey, option.value, event.target.checked)} />}
      </div>)}
    </div>
  </div>;
}

function HarnessResult({ title, result }) {
  if (!result) return null;
  const contextClass = result.context_source === "agent_discovered" ? "discovered" : result.context_source === "discovery_failed" ? "failed" : result.context_included ? "included" : "baseline";
  const contextLabel = result.context_source === "agent_discovered" ? "Agent discovered context" : result.context_source === "discovery_failed" ? "Discovery failed" : result.context_included ? "Context supplied" : "No context";
  return <article className="harness-result">
    <div className="harness-result-heading"><div><span className="mini-label">{title}</span><b>{result.agent_label || result.agent || "Agent run"}</b></div><span className={`context-chip ${contextClass}`}>{contextLabel}</span></div>
    <Markdown>{result.answer || "The agent returned no answer."}</Markdown>
    {result.query && <SqlResult sql={result.query} />}
    <details className="json-details"><summary>Run policy and trace</summary><DebugJson value={{ agent_config: result.agent_config, context_resolution: result.context_resolution, planner: result.planner, trace: result.trace }} /></details>
  </article>;
}

function AgentBuilder({ agents, catalog, connections, tools, defaultAgent, draft, setDraft, builtAgent, onBuild, question, setQuestion, evaluationTarget, setEvaluationTarget, onEvaluate, evaluationResult, busy }) {
  const domainOptions = (catalog?.domains || []).map((entry) => ({ value: entry.domain, label: entry.domain }));
  const defaultProfile = agents.find((item) => item.value === defaultAgent) || agents[0];
  return <div className="builder-layout">
    <div className="builder-card">
      <div className="builder-card-heading"><div><span className="mini-label">Governed draft</span><h3>Shape an agent for this workspace</h3></div><span className="debug-status">testing only</span></div>
      <p className="builder-copy">Write the instruction and policy the evaluator will use. Allow lists are a ceiling; disallow lists win when both are selected. Context is still discovered by the agent.</p>
      <div className="builder-grid">
        <label className="builder-field"><span>Agent key</span><input value={draft.key} onChange={(event) => setDraft((current) => ({ ...current, key: event.target.value }))} placeholder="revenue-agent" /></label>
        <label className="builder-field"><span>Display name</span><input value={draft.label} onChange={(event) => setDraft((current) => ({ ...current, label: event.target.value }))} placeholder="Revenue policy agent" /></label>
        <label className="builder-field full"><span>System prompt</span><textarea value={draft.system_prompt} onChange={(event) => setDraft((current) => ({ ...current, system_prompt: event.target.value }))} /></label>
        <label className="builder-field full"><span>Evaluation question</span><input value={question} onChange={(event) => setQuestion(event.target.value)} /></label>
      </div>
      <PolicyTable title="Connections" options={connections} draft={draft} setDraft={setDraft} allowKey="allowed_connections" denyKey="denied_connections" />
      <PolicyTable title="Tools" options={tools} draft={draft} setDraft={setDraft} allowKey="allowed_tools" denyKey="denied_tools" />
      <PolicyTable title="Context domains" options={domainOptions} draft={draft} setDraft={setDraft} allowKey="allowed_domains" denyKey="denied_domains" favorKey="favored_domains" />
      <div className="debug-actions"><button className="debug-button primary" type="button" onClick={() => onBuild({ ...draft })}>Build draft for evaluation</button><span className="builder-default-note">Default everywhere: <b>{defaultProfile?.label || "backend default"}</b></span></div>
      <div className="builder-evaluation">
        <div className="builder-evaluation-heading"><div><span className="mini-label">Try the draft</span><h4>Is this agent any good?</h4></div><span className="debug-status">not persisted</span></div>
        <p className="builder-copy">Run the current draft against a no-context discovery run, or compare it with another configured agent on the same question.</p>
        <div className="builder-evaluation-controls"><label className="builder-evaluation-field"><span>Compare against</span><select value={evaluationTarget} onChange={(event) => setEvaluationTarget(event.target.value)}><option value="__discover__">No context · agent discovers</option>{agents.map((item) => <option key={item.value} value={item.value}>{item.label}{item.value === defaultAgent ? " · default" : ""}</option>)}</select></label><button className="debug-button primary" type="button" disabled={busy} onClick={() => onEvaluate({ ...draft })}>Evaluate this agent</button></div>
        {evaluationResult?.draft && <div className="harness-results builder-evaluation-results"><HarnessResult title={evaluationResult.comparator ? "Draft agent · discovery" : "Draft agent · no-context discovery"} result={evaluationResult.draft} />{evaluationResult.comparator && <HarnessResult title={evaluationResult.comparatorTitle || "Comparison agent · discovery"} result={evaluationResult.comparator} />}</div>}
      </div>
    </div>
    <div className="builder-card builder-preview"><div className="builder-card-heading"><div><span className="mini-label">Agent contract</span><h3>{builtAgent ? "Draft ready" : "What will run"}</h3></div>{builtAgent && <span className="context-chip included">built</span>}</div><p className="builder-copy">The evaluator sends this policy with the request. Nothing is persisted, and no context bundle can be selected by hand.</p><DebugJson value={builtAgent || draft} empty="Fill out the draft to preview its contract." /></div>
  </div>;
}

function ValidationSummary({ validation }) {
  if (!validation) return null;
  const violations = validation.violations || [];
  return <div className={`validation-summary ${validation.status || "unknown"}`}><div><b>{validation.status || "Validation returned"}</b><span>{violations.length} violations</span></div><small>{validation.bundle_id ? `Bundle ${validation.bundle_id}` : "No bundle ID returned"}</small></div>;
}

function CandidateList({ discovery }) {
  const candidates = discovery?.candidates || [];
  if (!candidates.length) return <div className="empty-debug">No candidates yet. Type a question and discover.</div>;
  return <div className="candidate-list">
    {candidates.map((candidate, index) => {
      const signal = candidate.signal || {};
      const score = typeof signal.score === "number" ? signal.score.toFixed(3) : "—";
      const label = candidate.kind === "concept" ? candidate.term : candidate.domain;
      return <div className="candidate-item" key={`${candidate.kind}-${candidate.domain}-${candidate.term || ""}-${index}`}>
        <span className="candidate-rank">{index + 1}</span>
        <div className="candidate-body">
          <div className="candidate-head"><span className={`candidate-kind ${candidate.kind}`}>{candidate.kind}</span><b>{label || "—"}</b>{candidate.kind === "concept" && <small className="candidate-domain">in {candidate.domain}</small>}</div>
          <small className="candidate-signal">score {score} · matched on {signal.matched_on || candidate.kind} · {signal.model || "model"} · {signal.index_version || "index"}</small>
        </div>
        <span className="candidate-score" title={`ranking signal ${score}`}>{score}</span>
      </div>;
    })}
  </div>;
}

// A machine ref like "superset:dataset:e2e-real" or
// "table:postgres:analytics.public.churn" -> a friendly badge + the plain name,
// keeping the raw ref for the title tooltip so engineers lose nothing.
function friendlyRef(ref, role) {
  const raw = typeof ref === "string" ? ref : ref?.ref || "";
  const parts = String(raw).split(":");
  const name = parts.length > 1 ? parts[parts.length - 1] : raw;
  const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
  const badge = [parts.slice(0, -1).map(cap).join(" "), role].filter(Boolean).join(" · ");
  return { badge: badge || "Source", name, raw };
}

function ProposedCard({ task, proposal, onUndo, busy }) {
  const domain = (task.proposal_payload || {}).domain;
  const term = ((task.proposal_payload || {}).definition?.definitions || [])[0]?.term;
  return <div className="review-item review-proposed">
    <div className="review-meta"><span className="review-status proposed">Proposed</span>{domain && <span className="review-domain">{domain}</span>}{term && <span className="review-term-tag">“{term}”</span>}</div>
    <div className="review-proposed-body">
      {proposal.pr_url
        ? <><p>Opened a proposal PR into <b>{proposal.repository || "your context repo"}</b>. A human reviews and merges it in Git — that’s the approval.</p>
            <div className="review-proposed-actions"><a className="debug-button primary" href={proposal.pr_url} target="_blank" rel="noreferrer">Open pull request ↗</a><span className="review-branch" title={proposal.head_branch}>branch {proposal.head_branch} · {(proposal.commit_sha || "").slice(0, 8)}</span></div></>
        : <><p>Pushed branch <b>{proposal.head_branch}</b> ({(proposal.commit_sha || "").slice(0, 8)}) to your context repo. Open and merge the PR yourself — Hyperset never merges.</p></>}
      <ReviewRouting routing={(task.proposal_payload || {}).review_routing} />
      <button className="linklike review-undo" disabled={busy} onClick={() => onUndo(task.id)}>Re-open this card</button>
    </div>
  </div>;
}

// The task's owner as the UI shows it: the served `assignee` (an opaque
// subject@issuer, or null when unassigned), redacted at the DATA boundary
// (hy-6tsw9) so a credential can never reach the DOM even from a legacy value.
export function reviewOwner(task) {
  return redactDeep((task || {}).assignee) || null;
}

// The 'assigned to me' filter (hy-q7pth): the tasks whose owner is the caller's
// OWN identity -- the value the server computed when this browser last
// self-claimed. With no identity yet (never self-claimed) it matches nothing
// rather than guessing, so it never falsely narrows the queue.
export function assignedToMe(tasks, myIdentity) {
  if (!myIdentity) return [];
  return (tasks || []).filter((task) => reviewOwner(task) === myIdentity);
}

// A task reads as STALE when it is still awaiting review (open/in_progress) and its
// served `created_at` is older than `maxAgeDays` (hy-iomc). Derived from a field already
// served at SV 22 -- no new served field, no SCHEMA_VERSION move. A resolved/dismissed or
// undated task is never stale.
export function reviewIsStale(task, nowMs = Date.now(), maxAgeDays = 7) {
  const status = (task || {}).status;
  if (status !== "open" && status !== "in_progress") return false;
  const created = Date.parse((task || {}).created_at || "");
  if (Number.isNaN(created)) return false;
  return nowMs - created >= maxAgeDays * 86400000;
}

// The reviewer-queue filter (hy-iomc): narrow the ALREADY-served task list by status,
// urgency (`priority`), owner (mine/unassigned), and staleness -- all client-side over
// fields served at SV 22, so the served contract is byte-identical. `mineOnly` matches
// nothing without a verified identity (mirrors `assignedToMe`, so it never treats
// unassigned tasks as mine).
export function filterReviewTasks(tasks, filters = {}, myIdentity = "", nowMs = Date.now()) {
  const { status = "", priority = "", mineOnly = false, unassignedOnly = false, staleOnly = false } = filters;
  return (tasks || []).filter((task) => {
    if (status && task.status !== status) return false;
    if (priority !== "" && String(task.priority) !== String(priority)) return false;
    if (mineOnly && (!myIdentity || reviewOwner(task) !== myIdentity)) return false;
    if (unassignedOnly && reviewOwner(task) !== null) return false;
    if (staleOnly && !reviewIsStale(task, nowMs)) return false;
    return true;
  });
}

// The assist-class owner HINT for an UNASSIGNED task (hy-38mk8, S3): the served
// `suggested_assignee` (the prior in-domain reviewer the server inferred), or null
// when there is no suggestion. Redacted at the DATA boundary like the owner, and a
// SUGGESTION only -- the card shows it, the human confirms with the assign controls;
// it never assigns anyone on its own.
export function reviewSuggestedOwner(task) {
  return redactDeep((task || {}).suggested_assignee) || null;
}

// The served explanation for a suggestion (hy-38mk8 r2): the deterministic signal's
// summary, so the tooltip is the SERVER's rationale, not a hard-coded UI string an
// MCP/HTTP consumer never sees. Falls back to a generic line if an older server omits it.
export function reviewSuggestionSummary(task) {
  const rationale = (task || {}).suggested_assignee_rationale;
  return (rationale && rationale.summary) || "A suggestion you can confirm with Assign.";
}

// The exact current-vs-proposed change at detail (hy-z6zv), from the served `proposed_diff` --
// the diff that used to appear only inside the PR. Added/removed/changed per definition
// section, so a reviewer sees what the proposal does to the governed meaning before proposing.
export function ReviewMeaningDiff({ diff }) {
  if (!diff) return null;
  const sections = diff.sections || {};
  const names = Object.keys(sections);
  const grain = diff.grain;
  const label = (e) => e.term || e.name || e.ref || (e.from ? `${e.from} → ${e.to}` : JSON.stringify(e));
  if (!names.length && !grain) return <div className="review-diff"><span className="review-label">Changes vs current</span><p className="review-diff-none"><em>No change — the proposal matches the current governed meaning.</em></p></div>;
  return <div className="review-diff"><span className="review-label">Changes vs current</span>
    {names.map((name) => <div className="review-diff-section" key={name}>
      <span className="review-diff-name">{name}</span>
      {sections[name].added.map((e, i) => <div className="review-diff-row added" key={`a${i}`}>+ {label(e)}</div>)}
      {sections[name].removed.map((e, i) => <div className="review-diff-row removed" key={`r${i}`}>− {label(e)}</div>)}
      {sections[name].changed.map((c, i) => <div className="review-diff-row changed" key={`c${i}`}>~ {c.identity}</div>)}
    </div>)}
    {grain && <div className="review-diff-section"><span className="review-diff-name">grain</span><div className="review-diff-row changed">~ {String(grain.before)} → {String(grain.after)}</div></div>}
  </div>;
}

// The ephemeral proposed-context preview (hy-nauw): representative questions the proposed
// context is FOR, and deterministic regression checks -- rendered read-only, NOT SERVING.
export function ReviewPreviewPanel({ preview }) {
  if (!preview) return null;
  const questions = preview.representative_questions || [];
  const checks = preview.regression_checks || [];
  return <div className="review-preview">
    <span className="review-label">Preview · not serving</span>
    {questions.length > 0 && <div className="review-preview-qs">
      <span className="review-diff-name">Representative questions</span>
      {questions.map((q, i) => <div className="review-preview-q" key={i}>{q}</div>)}
    </div>}
    <div className="review-preview-checks">
      <span className="review-diff-name">Regression checks</span>
      {checks.map((c, i) => <div className={`review-check ${c.status}`} key={i}>
        <span className="review-check-status">{c.status}</span> {c.check}
        {c.detail?.length ? <ul>{c.detail.map((d, j) => <li key={j}>{d}</li>)}</ul> : null}
      </div>)}
    </div>
  </div>;
}

// The human decisions a reviewer may record on ONE cited source (matches the backend's
// CITATION_DECISIONS: include/exclude/approve/reject).
const CITATION_DECISIONS = ["include", "exclude", "approve", "reject"];

export function ReviewTaskItem({ task, onPropose, onEdit, onPreview, onRefine, onRequestEvidence, busy, canPropose, writeback = null, proposal, error, proposing, onUndo, myIdentity, onAssignSelf, onUnassign, onDecide, decisions = {}, decidingKey = "" }) {
  const payload = task.proposal_payload || {};
  const miss = payload.miss || {};
  const draft = payload.definition || {};
  const producedBy = payload.produced_by || {};
  const definitions = draft.definitions || [];
  const approvedSources = draft.approved_sources || [];
  const fields = draft.fields || [];
  const joins = draft.joins || [];
  const gathered = payload.gathered_sources || [];
  const evidenceByRef = new Map();
  [...gathered.map((s) => ({ ...friendlyRef(s.ref || s), signals: s.signals || [] })), ...approvedSources.map((e) => friendlyRef(e, e?.role))].forEach((item) => {
    if (!item.raw || !evidenceByRef.has(item.raw)) {
      evidenceByRef.set(item.raw || `evidence-${evidenceByRef.size}`, item);
      return;
    }
    const existing = evidenceByRef.get(item.raw);
    existing.signals = [...new Set([...(existing.signals || []), ...(item.signals || [])])];
  });
  const evidence = [...evidenceByRef.values()];
  // The cited sources a human may record a decision on: the task's affected observed assets
  // (a drift finding cites these and nothing else) UNION the evidence refs, deduped. Always
  // non-empty for a real task, so the decision control appears even when the payload carries
  // no gathered/approved sources (hy-n8ms3).
  const citations = [...new Set([...(task.affected_asset_ids || []), ...evidence.map((e) => e.raw).filter(Boolean)])];
  const owner = reviewOwner(task);
  const ownedByMe = !!owner && owner === myIdentity;
  const suggestedOwner = reviewSuggestedOwner(task);
  // Terminal cards remain visible as audit history, but every mutation control
  // must disappear for them. Missing status is treated as open for legacy
  // fixtures and older locally-created tasks.
  const mutable = task.status == null || task.status === "open" || task.status === "in_progress";
  const [form, setForm] = useState(null); // null = not editing
  const [preview, setPreview] = useState(null); // the fetched ephemeral preview, or null
  const [previewing, setPreviewing] = useState(false);
  const runPreview = () => { setPreviewing(true); Promise.resolve(onPreview(task.id)).then(setPreview).finally(() => setPreviewing(false)); };
  const [refineText, setRefineText] = useState(null); // null = closed; a string = the feedback box is open
  const sendRefine = () => { onRefine(task.id, refineText || ""); setRefineText(null); };
  const startEdit = () => setForm({
    defs: (definitions.length ? definitions : [{ term: "", statement: "" }]).map((d) => ({ term: d.term || "", statement: d.statement || "" })),
    advText: (() => { const { definitions: _d, ...rest } = draft; return JSON.stringify(rest, null, 2); })(),
    error: "",
  });
  const saveEdit = () => {
    const { definitions: _drop, ...rest } = draft;
    let extra = rest;
    if (form.advText.trim()) { try { extra = JSON.parse(form.advText); } catch { setForm((f) => ({ ...f, error: "Advanced JSON is not valid." })); return; } }
    onEdit(task.id, { ...extra, definitions: form.defs.filter((d) => d.term.trim()) });
    setForm(null);
  };

  if (proposal) return <ProposedCard task={task} proposal={proposal} onUndo={onUndo} busy={busy} />;

  return <div className="review-item">
    <div className="review-meta">
      <span className={`review-status ${form ? "editing" : ""}`}>{form ? "Editing" : mutable ? "Needs review" : task.status}</span>
      {payload.domain && <span className="review-domain">{payload.domain}</span>}
      <span className="review-priority" title="Urgency (priority)">{`Priority ${task.priority}`}</span>
      {reviewIsStale(task) && <span className="review-stale" title="Awaiting review for over a week">Stale</span>}
      {miss.resolve_miss_id && <span className="review-missid" title={miss.resolve_miss_id}>from a miss</span>}
      {owner
        ? <span className={`review-owner ${ownedByMe ? "mine" : ""}`} title={owner}>{ownedByMe ? "Assigned to you" : `Owned by ${owner}`}</span>
        : <span className="review-owner unassigned">Unassigned</span>}
      {!owner && suggestedOwner && <span className="review-suggested" title={reviewSuggestionSummary(task)}>Suggested: {suggestedOwner}</span>}
    </div>

    <div className="review-hero">
      {form
        ? <>
            <span className="review-label">Edit the definition</span>
            {form.defs.map((d, i) => <div className="review-editfield" key={i}>
              <label className="review-field"><span>Term</span><input value={d.term} onChange={(e) => setForm((f) => ({ ...f, defs: f.defs.map((x, j) => j === i ? { ...x, term: e.target.value } : x) }))} /></label>
              <label className="review-field"><span>Plain-language definition</span><textarea rows={3} value={d.statement} onChange={(e) => setForm((f) => ({ ...f, defs: f.defs.map((x, j) => j === i ? { ...x, statement: e.target.value } : x) }))} /><small className="review-hint-text">Write it so a new analyst understands it — no SQL needed.</small></label>
            </div>)}
            <details className="review-advanced"><summary>Advanced: sources, fields &amp; joins (JSON)</summary><textarea className="review-edit" rows={9} value={form.advText} onChange={(e) => setForm((f) => ({ ...f, advText: e.target.value }))} /></details>
            {form.error && <small className="review-error">{form.error}</small>}
          </>
        : <>
            <span className="review-label">Definition to review</span>
            {definitions.length ? definitions.map((entry, i) => <div className="review-definition" key={entry.term || i}><b>{entry.term}</b><p>{entry.statement || <em>(no statement drafted yet — Edit to write one)</em>}</p></div>) : <p className="review-definition"><em>(no definition drafted)</em></p>}
            <span className="review-trust" title={`${producedBy.producer || "authoring"}${producedBy.model ? ` · ${producedBy.model}` : ""}`}>{payload.edited_by_human ? "✓ Edited by you" : "✎ AI draft · not yet reviewed by you"}</span>
          </>}
    </div>

    {!form && <div className="review-current"><span className="review-label">Current governed meaning</span>
      {task.current_meaning
        ? (task.current_meaning.definitions || []).map((entry, i) => <div className="review-definition" key={entry.term || i}><b>{entry.term}</b><p>{entry.statement || <em>(no statement)</em>}</p></div>)
        : <p className="review-definition"><em>Nothing governed for this domain yet — this proposal would introduce it.</em></p>}
    </div>}
    {!form && <ReviewMeaningDiff diff={task.proposed_diff} />}
    {!form && preview && <ReviewPreviewPanel preview={preview} />}

    {!form && miss.question && <div className="review-why"><span className="review-label">Why this is here</span><p>Someone asked “{miss.question}” and no governed context answered it{(task.uncertainty?.undeclared_concepts || []).length ? `. Undeclared: ${(task.uncertainty.undeclared_concepts).join(", ")}` : ""}.</p></div>}

    {!form && evidence.length > 0 && <div className="review-evidence">
      <span className="review-label">Evidence · {evidence.length} observed source{evidence.length > 1 ? "s" : ""}</span>
      {evidence.map((e, i) => <div className="evidence-row" key={i}><span className="evidence-badge">{e.badge}</span><span className="evidence-name" title={e.raw}>{e.name}</span>{e.signals?.length ? <span className="evidence-signals">{e.signals.join(" · ")}</span> : null}</div>)}
    </div>}
    {!form && mutable && onDecide && citations.length > 0 && <div className="review-citations">
      <span className="review-label">Cited sources · record your decision</span>
      {citations.map((ref) => <div className="citation-row" key={ref}>
        <div className="citation-source">
          <span className="citation-ref" title={ref}>{friendlyRef(ref).name}</span>
          {decisions[ref] ? <span className="evidence-decided" title={`Recorded ${decisions[ref].decision} · ${decisions[ref].decided_by || "you"}`}>Decided: {decisions[ref].decision}</span> : null}
        </div>
        <div className="citation-decisions" role="group" aria-label={`Decision for cited source ${ref}`}>
          {CITATION_DECISIONS.map((choice) => <button key={choice} type="button" className={`citation-decide ${decisions[ref]?.decision === choice ? "active" : ""}`} aria-label={`${choice} cited source ${ref}`} aria-pressed={decisions[ref]?.decision === choice} disabled={busy || decidingKey === `${task.id}::${ref}`} title={`Record a '${choice}' decision for ${ref}`} onClick={() => onDecide(task.id, ref, ref, choice)}>{choice}</button>)}
        </div>
      </div>)}
      <small className="review-note">Recording a decision writes an audit row only — it approves, merges, and changes no governed context (ADR 0012).</small>
    </div>}
    {!form && fields.length > 0 && <div className="review-reflines"><span className="review-label">Fields</span>{fields.map((f, i) => <div className="review-refline" key={i}>{f.name} = <code>{f.expression}</code></div>)}</div>}
    {!form && joins.length > 0 && <div className="review-reflines"><span className="review-label">Joins</span>{joins.map((j, i) => <div className="review-refline" key={i}>{j.from} → {j.to} ({j.type || "join"})</div>)}</div>}

    {error && <div className="review-error-inline">⚠ Couldn’t propose: {error} <a className="linklike" href="/admin/">Configure write-back ↗</a></div>}

    <div className="review-actions">
      {form
        ? <><button className="debug-button primary" disabled={busy} onClick={saveEdit}>Save changes</button><button className="debug-button" disabled={busy} onClick={() => setForm(null)}>Cancel</button></>
        : mutable
          ? <>{!owner && <button className="debug-button" disabled={busy} onClick={() => onAssignSelf(task.id)}>Assign to me</button>}
            {ownedByMe && <button className="debug-button" disabled={busy} onClick={() => onUnassign(task.id)}>Unassign</button>}
            <button className="debug-button" disabled={busy} onClick={startEdit}>Edit definition</button>
            <button className="debug-button" disabled={busy} title="Ask the assist agent to redraft this definition from your feedback" onClick={() => setRefineText(refineText === null ? "" : null)}>Refine with agent</button>
            <button className="debug-button" disabled={busy} title="Re-gather the observed evidence for this task" onClick={() => onRequestEvidence(task.id)}>Re-gather evidence</button>
            <button className="debug-button" disabled={busy || previewing} title="A read-only preview of the proposed context — not serving" onClick={runPreview}>{previewing ? "Previewing…" : preview ? "Refresh preview" : "Preview"}</button>
            <button className="debug-button primary" disabled={busy || proposing || !canPropose} title={canPropose ? "Open a proposal-only PR into the configured context repo" : "Configure a write-back repository in Settings first"} onClick={() => onPropose(task.id)}>{proposing ? "Proposing…" : "Propose to Git →"}</button></>
          : <span className="review-readonly">Read-only history · {task.status}</span>}
    </div>
    {!form && refineText !== null && <div className="review-refine">
      <label className="review-field"><span>Feedback for the assist agent</span>
        <textarea rows={3} value={refineText} onChange={(e) => setRefineText(e.target.value)} placeholder="e.g. be more precise about the period" /></label>
      <div className="review-actions">
        <button className="debug-button primary" disabled={busy} onClick={sendRefine}>Send to agent</button>
        <button className="debug-button" disabled={busy} onClick={() => setRefineText(null)}>Cancel</button>
      </div>
    </div>}
    {!form && canPropose && !writeback && <small className="review-note">ⓘ The server checks this task’s domain target first, then the default target, and fails closed if neither is configured. Proposing opens a PR only — a human merges in Git (ADR 0012).</small>}
    {!form && canPropose && writeback && <small className="review-note">ⓘ Proposing opens a PR only — a human merges in Git (ADR 0012).</small>}
  </div>;
}

function ReviewTaskList({ data, onPropose, onEdit, onPreview, onRefine, onRequestEvidence, busy, canPropose, writeback = null, proposedByTask, errorByTask, proposingId, onUndo, myIdentity, onAssignSelf, onUnassign, filters, onDecide, decisionsByTask = {}, decidingKey = "" }) {
  const tasks = data?.tasks || [];
  if (!tasks.length) return <div className="review-empty"><b>You’re all caught up.</b><span>No context gaps waiting. When a question hits an undefined concept, Hyperset drafts a definition and it lands here to confirm.</span></div>;
  const scoped = filterReviewTasks(tasks, filters, myIdentity);
  if (!scoped.length) return <div className="review-empty"><b>No tasks match these filters.</b><span>Clear or widen the filters above to see more of the queue.</span></div>;
  const open = scoped.filter((t) => !proposedByTask[t.id]);
  const proposed = scoped.filter((t) => proposedByTask[t.id]);
  const ordered = [...open, ...proposed];
  return <div className="review-list">
    {ordered.map((task) => <ReviewTaskItem key={task.id} task={task} onPropose={onPropose} onEdit={onEdit} onPreview={onPreview} onRefine={onRefine} onRequestEvidence={onRequestEvidence} busy={busy} canPropose={canPropose} writeback={writeback} proposal={proposedByTask[task.id]} error={errorByTask[task.id]} proposing={proposingId === task.id} onUndo={onUndo} myIdentity={myIdentity} onAssignSelf={onAssignSelf} onUnassign={onUnassign} onDecide={onDecide} decisions={decisionsByTask[task.id] || {}} decidingKey={decidingKey} />)}
  </div>;
}

function AdminDebugPanel({ active, model, models, agents, connections, tools, defaultAgent, bundle, setBundle, publicView = false }) {
  const [environment, setEnvironment] = useState(null);
  const [catalog, setCatalog] = useState(null);
  const [selectedDomain, setSelectedDomain] = useState("");
  const [selectedConcept, setSelectedConcept] = useState("");
  const [question, setQuestion] = useState("Which source and rules should an analyst use for recognized revenue by region?");
  const [history, setHistory] = useState(null);
  const [discovery, setDiscovery] = useState(null);
  const [validation, setValidation] = useState(null);
  const [harness, setHarness] = useState(null);
  const [evaluatorContextMode, setEvaluatorContextMode] = useState("discover");
  const [evaluatorAgent, setEvaluatorAgent] = useState(defaultAgent || "");
  const [builtAgent, setBuiltAgent] = useState(null);
  const [builderEvaluationTarget, setBuilderEvaluationTarget] = useState("__discover__");
  const [builderEvaluation, setBuilderEvaluation] = useState(null);
  const [agentDraft, setAgentDraft] = useState({
    key: "custom-agent",
    label: "Custom governed agent",
    system_prompt: "Answer with the approved context first. State what is governed, what was observed, and what remains uncertain.",
    allowed_connections: [],
    denied_connections: [],
    allowed_tools: [],
    denied_tools: [],
    allowed_domains: [],
    denied_domains: [],
    favored_domains: [],
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const selectedEntry = catalog?.domains?.find((entry) => entry.domain === selectedDomain);
  const concepts = selectedEntry?.concepts || [];
  useEffect(() => { if (active === "environment" && !environment) requestJson("/demo/status", null, "GET").then(setEnvironment).catch((reason) => setError(reason.message)); }, [active, environment]);
  const loadCatalog = async (force = false) => { if (catalog && !force) return; setError(""); try { const data = await requestJson("/v0/list_context_catalog", { limit: 50, offset: 0 }); setCatalog(data); const first = data.domains?.[0]; if (first && !selectedDomain) { setSelectedDomain(first.domain); setSelectedConcept(first.concepts?.[0] || ""); } } catch (reason) { setError(reason.message); } };
  useEffect(() => { if (["catalog", "bundle", "validation", "builder", "harness", "graph"].includes(active) && !catalog) loadCatalog(); }, [active, catalog]);
  useEffect(() => { if (!evaluatorAgent && defaultAgent) setEvaluatorAgent(defaultAgent); }, [defaultAgent, evaluatorAgent]);
  const chooseDomain = (domain) => { const entry = catalog?.domains?.find((item) => item.domain === domain); setSelectedDomain(domain); setSelectedConcept(entry?.concepts?.[0] || ""); setBundle(null); setValidation(null); setHarness(null); };
  const chooseConcept = (concept) => { setSelectedConcept(concept); setBundle(null); setValidation(null); setHarness(null); };
  const discover = async () => { if (!question.trim()) { setError("Type a question first."); return; } setBusy(true); setError(""); try { setDiscovery(await requestJson("/v0/discover_analytics_context", { query: question, limit: 20 })); } catch (reason) { setError(reason.message); } finally { setBusy(false); } };
  const resolve = async () => { if (!selectedDomain) { setError("Choose a synced domain first."); return null; } setBusy(true); setError(""); try { const data = await requestJson("/v0/resolve_analytics_context", { query: question, directive: { domains: [selectedDomain], concepts: selectedConcept ? [selectedConcept] : [] } }); setBundle(data); return data; } catch (reason) { setError(reason.message); return null; } finally { setBusy(false); } };
  const loadHistory = async () => { const authority = selectedEntry?.context_authority || selectedEntry?.source || {}; if (!authority.repository || !authority.ref || !authority.path) { setError("This catalog entry did not return a complete Git source identity."); return; } setBusy(true); try { const params = new URLSearchParams(authority); setHistory(await requestJson(`/v0/context/history?${params}`, null, "GET")); } catch (reason) { setError(reason.message); } finally { setBusy(false); } };
  const validate = async () => { if (!bundle) return; setBusy(true); setError(""); const instructions = bundle.instructions || {}; try { setValidation(await requestJson("/v0/validate_analytics_plan", { query: bundle.request.query, directive: bundle.request.directive, bundle_id: bundle.bundle_id, source_refs: (instructions.approved_sources || []).map((item) => typeof item === "string" ? item : item.ref).filter(Boolean), fields: instructions.fields || [], joins: instructions.joins || [], filters: instructions.filters || [], grain: instructions.grain || null, checks: instructions.validations || [] })); } catch (reason) { setError(reason.message); } finally { setBusy(false); } };
  const selectedEvaluatorConfig = evaluatorAgent === "__custom__" ? builtAgent : null;
  const harnessRequest = (selectedModel, { agentValue, agentConfig, mode, selectedBundle = null, questionValue = question }) => requestJson("/demo/ask", { question: questionValue, provider: selectedModel.provider, model, agent: agentConfig ? defaultAgent : agentValue, agent_config: agentConfig, catalog_domains: (catalog?.domains || []).map((entry) => entry.domain), mode, bundle: selectedBundle, selection_trace: null });
  const runHarness = async () => { setBusy(true); setError(""); try { const selectedModel = models.find((item) => item.value === model); if (!selectedModel) throw new Error("The backend has not published a model configuration."); const useProvidedContext = evaluatorContextMode === "provided"; if (useProvidedContext && !bundle) throw new Error("Resolve a context bundle before evaluating with selected context."); setHarness(await harnessRequest(selectedModel, { agentValue: evaluatorAgent, agentConfig: selectedEvaluatorConfig, mode: useProvidedContext ? "governed" : "discover", selectedBundle: useProvidedContext ? bundle : null })); } catch (reason) { setError(reason.message); } finally { setBusy(false); } };
  const compareHarness = async () => { setBusy(true); setError(""); try { const selectedModel = models.find((item) => item.value === model); if (!selectedModel) throw new Error("The backend has not published a model configuration."); if (!bundle) throw new Error("Resolve a context bundle before comparing it with no-context discovery."); const shared = { agentValue: evaluatorAgent, agentConfig: selectedEvaluatorConfig }; const [withContext, withoutContext] = await Promise.all([harnessRequest(selectedModel, { ...shared, mode: "governed", selectedBundle: bundle }), harnessRequest(selectedModel, { ...shared, mode: "discover" })]); setHarness({ with_context: withContext, without_context: withoutContext }); } catch (reason) { setError(reason.message); } finally { setBusy(false); } };
  const evaluateBuilder = async (draft) => { setBusy(true); setError(""); setBuiltAgent(draft); try { const selectedModel = models.find((item) => item.value === model); if (!selectedModel) throw new Error("The backend has not published a model configuration."); const draftResult = await harnessRequest(selectedModel, { agentValue: defaultAgent, agentConfig: draft, mode: "discover" }); if (builderEvaluationTarget === "__discover__") { setBuilderEvaluation({ draft: draftResult }); } else { const comparator = await harnessRequest(selectedModel, { agentValue: builderEvaluationTarget, mode: "discover" }); const comparatorProfile = agents.find((item) => item.value === builderEvaluationTarget); setBuilderEvaluation({ draft: draftResult, comparator, comparatorTitle: `${comparatorProfile?.label || "Comparison agent"} · discovery` }); } } catch (reason) { setError(reason.message); } finally { setBusy(false); } };
  if (active === "chat") return null;
  return <section className="debug-panel">
    <div className="debug-panel-heading"><div><span className="eyebrow">{publicView ? "Context workspace" : "Admin diagnostics"}</span><h2>{DEBUG_TABS.find(([key]) => key === active)?.[1]}</h2></div><span className="debug-status">{busy ? "working…" : "read-only"}</span></div>
    {error && <div className="error-banner">{error}</div>}
    {active === "mcp" && <McpSetupWizard />}
    {active === "environment" && <><p className="debug-lede">See the connected API, model, connector, and warehouse probes that power this playground.</p><button className="debug-button" onClick={() => { setEnvironment(null); setError(""); requestJson("/demo/status", null, "GET").then(setEnvironment).catch((reason) => setError(reason.message)); }}>Refresh environment</button><StatusGrid data={environment} /><DebugJson value={environment} /></>}
    {active === "catalog" && <><p className="debug-lede">Browse the governed domains and concepts currently synced into the context catalog.</p><button className="debug-button" onClick={() => { setCatalog(null); loadCatalog(true); }}>Refresh catalog</button><div className="catalog-list">{(catalog?.domains || []).map((entry) => <button key={entry.domain} className={`catalog-item ${selectedDomain === entry.domain ? "selected" : ""}`} onClick={() => chooseDomain(entry.domain)}><b>{entry.domain}</b><span>{entry.title}</span><small>{(entry.concepts || []).join(" · ") || "No concepts"}</small></button>)}</div><DebugJson value={catalog} /></>}
    {active === "discover" && <><p className="debug-lede">Type an ordinary question. Discovery ranks the catalog's domains and concepts by relevance so a planner can reach the right governed slice — assist-class and non-authoritative: it says where to look, never that an answer is governed. Send the exact names it surfaces through the bundle resolver.</p><div className="debug-form"><label>Question<input value={question} onChange={(event) => setQuestion(event.target.value)} /></label></div><div className="debug-actions"><button className="debug-button primary" disabled={busy || !question.trim()} onClick={discover}>Discover candidates</button></div><CandidateList discovery={discovery} /><DebugJson value={discovery} empty="Discovery has not run yet." /></>}
    {active === "bundle" && <><p className="debug-lede">Resolve a connected ContextBundle from a question and inspect its relationships, authority, and Git history.</p><div className="debug-form"><label>Question<input value={question} onChange={(event) => setQuestion(event.target.value)} /></label></div><DomainPicker catalog={catalog} selectedDomain={selectedDomain} selectedConcept={selectedConcept} onDomainChange={chooseDomain} onConceptChange={chooseConcept} /><div className="debug-actions"><button className="debug-button primary" disabled={busy || !selectedDomain} onClick={resolve}>Resolve bundle</button><button className="debug-button" disabled={busy} onClick={loadHistory}>Load Git history</button></div><GraphSummary bundle={bundle} selection={null} /><DebugJson value={bundle} /><DebugJson value={history} empty="No history loaded." /></>}
    {active === "validation" && <><p className="debug-lede">Choose a governed domain, resolve its exact bundle, and validate the plan against that bundle.</p><div className="debug-form"><label>Question<input value={question} onChange={(event) => setQuestion(event.target.value)} /></label></div><DomainPicker catalog={catalog} selectedDomain={selectedDomain} selectedConcept={selectedConcept} onDomainChange={chooseDomain} onConceptChange={chooseConcept} /><div className="debug-actions"><button className="debug-button" disabled={busy || !selectedDomain} onClick={resolve}>Resolve selected domain</button><button className="debug-button primary" disabled={busy || !bundle} onClick={validate}>Validate selected plan</button></div>{bundle && <GraphSummary bundle={bundle} selection={null} />}<ValidationSummary validation={validation} /><DebugJson value={validation} empty={bundle ? "Validation has not run yet." : "Resolve a bundle first."} /></>}
    {active === "console" && <ApiConsole request={requestJson} />}
    {active === "builder" && <><p className="debug-lede">Build a small governed agent contract and evaluate it immediately on a question. Context is still discovered by the agent; nothing is persisted.</p><AgentBuilder agents={agents} catalog={catalog} connections={connections} tools={tools} defaultAgent={defaultAgent} draft={agentDraft} setDraft={setAgentDraft} builtAgent={builtAgent} question={question} setQuestion={setQuestion} evaluationTarget={builderEvaluationTarget} setEvaluationTarget={setBuilderEvaluationTarget} evaluationResult={builderEvaluation} busy={busy} onBuild={(draft) => { setBuiltAgent(draft); setEvaluatorAgent("__custom__"); setHarness(null); }} onEvaluate={evaluateBuilder} /></>}
    {active === "harness" && <><p className="debug-lede">Start with no context passed in: Hyperset first reads the catalog and discovers the governed bundle itself. Switch to selected context when you want to compare a pre-resolved bundle against that discovery path.</p><div className="debug-form evaluator-form"><label>Agent<select value={evaluatorAgent} onChange={(event) => { setEvaluatorAgent(event.target.value); setHarness(null); }}>{agents.map((item) => <option key={item.value} value={item.value}>{item.label}{item.value === defaultAgent ? " · default" : ""}</option>)}{builtAgent && <option value="__custom__">{builtAgent.label} · built draft</option>}</select></label><label>Context input<select value={evaluatorContextMode} onChange={(event) => { setEvaluatorContextMode(event.target.value); setHarness(null); }}><option value="discover">No context · agent discovers</option><option value="provided">Use selected context</option></select></label><label>Question<input value={question} onChange={(event) => setQuestion(event.target.value)} /></label></div>{evaluatorContextMode === "provided" ? <DomainPicker catalog={catalog} selectedDomain={selectedDomain} selectedConcept={selectedConcept} onDomainChange={chooseDomain} onConceptChange={chooseConcept} /> : <div className="context-mode-note"><span className="summary-dot purple" /><span><b>No bundle is passed to this run.</b> The agent uses catalog discovery and context resolution before answering.</span></div>}<div className="debug-actions"><button className="debug-button primary" disabled={busy || !evaluatorAgent || (evaluatorAgent === "__custom__" && !builtAgent) || (evaluatorContextMode === "provided" && !bundle)} onClick={runHarness}>{evaluatorContextMode === "provided" ? "Evaluate selected context" : "Evaluate with no context"}</button>{evaluatorContextMode === "provided" && <button className="debug-button" disabled={busy || !bundle || !evaluatorAgent || (evaluatorAgent === "__custom__" && !builtAgent)} onClick={compareHarness}>Compare against no-context</button>}</div>{evaluatorContextMode === "provided" && bundle && <GraphSummary bundle={bundle} selection={null} />} {harness?.with_context ? <div className="harness-results"><HarnessResult title="Selected context" result={harness.with_context} /><HarnessResult title="No-context discovery" result={harness.without_context} /></div> : harness ? <HarnessResult title={evaluatorContextMode === "provided" ? "Selected context run" : "No-context discovery run"} result={harness} /> : <DebugJson value={harness} empty="Run an evaluation to inspect the selected agent." />}</>}
    {active === "graph" && <><p className="debug-lede">Choose a governed domain to render its actual nodes, relationships, and evidence graph.</p><DomainPicker catalog={catalog} selectedDomain={selectedDomain} selectedConcept={selectedConcept} onDomainChange={chooseDomain} onConceptChange={chooseConcept} /><div className="debug-actions"><button className="debug-button primary" disabled={busy || !selectedDomain} onClick={resolve}>Load domain graph</button></div>{bundle && <><div className="graph-debug-meta"><b>{bundle.bundle_id}</b><span>{(bundle.domain_graph?.nodes || []).length} nodes · {(bundle.domain_graph?.edges || []).length} relationships</span></div><DomainGraphView bundle={bundle} /><div className="debug-graph-grid"><div><h3>Nodes</h3>{(bundle.domain_graph?.nodes || []).map((node) => <div className="debug-row" key={node.id}><b>{node.label || node.id}</b><small>{node.kind || "node"}</small></div>)}</div><div><h3>Edges</h3>{(bundle.domain_graph?.edges || []).map((edge, index) => <div className="debug-row" key={`${edge.from}-${edge.to}-${index}`}><b>{edge.relation || "related"}</b><small>{edge.from} → {edge.to}</small></div>)}</div></div><DebugJson value={bundle.domain_graph} /></>} {!bundle && <div className="empty-debug">Choose a domain and load its graph.</div>}</>}
    {active === "hive" && <HiveMindGraph requestJson={requestJson} />}
  </section>;
}

function ShellPlaceholder({ eyebrow, title, copy, children }) {
  return <section className="shell-page shell-placeholder">
    <span className="eyebrow">{eyebrow}</span>
    <h1>{title}</h1>
    <p>{copy}</p>
    {children}
  </section>;
}

function DocsPage() {
  const [document, setDocument] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let mounted = true;
    requestJson("/v0/docs/getting-started", null, "GET")
      .then((data) => { if (mounted) setDocument(data); })
      .catch((reason) => { if (mounted) setError(reason.message || "Could not load local documentation."); });
    return () => { mounted = false; };
  }, []);
  return <section className="shell-page docs-page">
    <div className="shell-page-heading"><div><span className="eyebrow">Documentation</span><h1>Get started inside Hyperset.</h1><p>This is the deployment’s local documentation, rendered in the app so you can follow setup without leaving the workspace.</p></div><span className="docs-local-badge">Local docs</span></div>
    {error && <div className="error-banner">{error}</div>}
    {!document && !error && <div className="empty-debug">Loading local documentation…</div>}
    {document && <div className="docs-layout">
      <aside className="docs-nav" aria-label="Documentation sections"><a href="#getting-started">Getting started</a><a href="#connecting-an-mcp-client">Connect MCP</a><a href="#production">Production notes</a><span>{document.path}</span></aside>
      <article className="docs-content"><Markdown>{document.markdown}</Markdown></article>
    </div>}
  </section>;
}

function threadRestorePayload(thread) {
  const messages = Array.isArray(thread.messages) && thread.messages.length
    ? thread.messages
    : [
      { id: `${thread.id}-question`, role: "user", content: thread.question, createdAt: thread.createdAt },
      { id: `${thread.id}-answer`, role: "assistant", content: thread.answer, createdAt: thread.createdAt },
    ].filter((message) => message.content);
  return {
    question: thread.question,
    answer: thread.answer,
    messages,
    agent: thread.agent ?? null,
    model: thread.model ?? null,
    governedOnly: thread.governedOnly !== false,
  };
}

export function RecentThreadsPage() {
  const [threads, setThreads] = useState([]);
  useEffect(() => {
    // Defense in depth: threads are already redacted on the persist boundary (saveThreadTurn),
    // but redact again on read so a legacy cleartext record never renders a credential.
    try { setThreads(redactDeep(JSON.parse(localStorage.getItem("hyperset-threads") || "[]"))); } catch { setThreads([]); }
  }, []);
  // Reopen RESTORES the thread's run state (agent / model / run mode), not just the
  // question (hy-87n1): hand the settings to the next chat mount, then load /playground/.
  const openThread = (thread) => writeThreadRestore(localStorage, threadRestorePayload(thread));
  const runModeLabel = (thread) => thread.governedOnly === false ? "Governed + observed" : "Governed only";
  return <section className="shell-page threads-page">
    <div className="shell-page-heading"><div><span className="eyebrow">Recent threads</span><h1>Pick up where you left off.</h1><p>Completed local Playground turns are kept in this browser so you can reopen the question and its run settings without losing the context of the work.</p></div><span className="docs-local-badge">Browser-local</span></div>
    {threads.length === 0
      ? <div className="empty-state-card"><b>No completed questions yet.</b><span>Ask something in New chat and it will appear here when the answer finishes.</span><a className="debug-button primary" href="/playground/">Start a question</a></div>
      : <div className="thread-list">{threads.map((thread) => <article className="thread-card" key={thread.id}><div><span className="mini-label">{new Date(thread.createdAt).toLocaleString()}</span><h2>{thread.question}</h2><p>{thread.answer ? thread.answer.slice(0, 220) : "Answer saved locally."}{thread.answer?.length > 220 ? "…" : ""}</p><small className="thread-settings">{runModeLabel(thread)}{thread.agent ? ` · ${thread.agent}` : ""}{thread.model ? ` · ${thread.model}` : ""}</small></div><a className="debug-button" href="/playground/" onClick={() => openThread(thread)}>Open thread</a></article>)}</div>}
  </section>;
}

// Recent chats belong to the conversation surface, not a second destination.
// Keep this compact and browser-local: reopening a chat still restores the
// original agent/model/run mode through the same redacted handoff as the
// archived full-page view above.
export function RecentThreadsPanel() {
  const [threads, setThreads] = useState([]);
  const load = React.useCallback(() => {
    try {
      setThreads(redactDeep(JSON.parse(localStorage.getItem("hyperset-threads") || "[]")));
    } catch {
      setThreads([]);
    }
  }, []);
  useEffect(() => {
    load();
    const refreshOnFocus = () => load();
    window.addEventListener("focus", refreshOnFocus);
    const interval = window.setInterval(load, 3000);
    return () => {
      window.removeEventListener("focus", refreshOnFocus);
      window.clearInterval(interval);
    };
  }, [load]);
  const openThread = (thread) => writeThreadRestore(localStorage, threadRestorePayload(thread));
  const recent = threads;
  return <aside className="recent-chats-panel" aria-label="Recent chats">
    <div className="recent-chats-heading">
      <div><span className="mini-label">Your workspace</span><h2>Recent chats</h2></div>
      <span className="recent-chats-count">{threads.length}</span>
    </div>
    {recent.length ? <div className="recent-chats-list">{recent.map((thread) => <a className="recent-chat-item" key={thread.id} href="/playground/" onClick={() => openThread(thread)}>
      <strong>{thread.question || "Untitled question"}</strong>
      <small>{thread.answer ? thread.answer.slice(0, 92) : "Answer saved locally."}{thread.answer?.length > 92 ? "…" : ""}</small>
    </a>)}</div> : <p className="recent-chats-empty">Your completed questions will appear here.</p>}
    <p className="recent-chats-note">Stored only in this browser. Secrets are redacted before saving.</p>
  </aside>;
}

export function LiveChatPage({ ...chatProps }) {
  return <>
    <style>{`
      .live-chat-layout.recent-chats-left { grid-template-columns: 248px minmax(0, 1fr); }
      @media (max-width: 900px) {
        .live-chat-layout.recent-chats-left { grid-template-columns: 1fr; }
        .live-chat-layout.recent-chats-left .live-chat-column { order: 1; }
        .live-chat-layout.recent-chats-left .recent-chats-panel { order: 2; }
      }
    `}</style>
    <div className="live-chat-layout recent-chats-left" data-testid="live-chat-layout">
      <RecentThreadsPanel />
      <div className="live-chat-column"><HypersetChat {...chatProps} /></div>
    </div>
  </>;
}

function HelpPage() {
  return <section className="shell-page help-page">
    <span className="eyebrow">Help</span><h1>Understand the governed path.</h1><p>Hyperset keeps the answer, the context it used, and the operational details separate so you can decide what to trust.</p>
    <div className="help-grid"><article><span className="help-step">01</span><h2>Ask in plain language</h2><p>Start with the metric, domain, or source question you need answered. You do not have to know the internal graph shape.</p></article><article><span className="help-step">02</span><h2>Inspect context</h2><p>Use Explore to see definitions, relationships, approved sources, and the serving commit before you rely on a result.</p></article><article><span className="help-step">03</span><h2>Read the answer</h2><p>Streaming shows the answer first. Technical trace, SQL, and provenance are available as collapsed details when you need them.</p></article></div>
    <div className="help-callout"><b>Need the integration guide?</b><span>Open the live MCP setup in Settings.</span><a className="debug-button" href={MCP_DOCS_URL}>Open MCP settings</a></div>
  </section>;
}

function ProfilePage({ runtimeConfig, backendHealthy, auth }) {
  const model = runtimeConfig.models.find((item) => item.value === runtimeConfig.default_model);
  const agent = runtimeConfig.agents.find((item) => item.value === runtimeConfig.default_agent);
  return <section className="shell-page profile-page">
    <div className="shell-page-heading"><div><span className="eyebrow">Profile</span><h1>Your local workspace.</h1><p>Deployment configuration is visible here instead of being hidden behind a dead settings tab.</p></div><span className={`status-badge ${backendHealthy ? "ready" : "blocked"}`}>{backendHealthy ? "Connected" : "Offline"}</span></div>
    <div className="profile-grid"><article><span className="mini-label">Default agent</span><h2>{agent?.label || "Not configured"}</h2><p>{agent?.detail || "Configure HYPERSET_PLAYGROUND_AGENTS_JSON to publish an agent."}</p></article><article><span className="mini-label">Default model</span><h2>{model?.label || "Not configured"}</h2><p>Model selection is deployment-controlled and never exposes provider credentials.</p></article><article><span className="mini-label">Authentication</span><h2>{auth?.enabled ? "OIDC enabled" : "Loopback demo"}</h2><p>{auth?.enabled ? "Login and logout are backed by the configured OIDC flow." : "Login is intentionally disabled for this local loopback demo; it is not a broken link."}</p></article></div>
  </section>;
}

function HomePage() {
  const navigate = useNavigate();
  return <section className="shell-page shell-home">
    <span className="eyebrow">Home</span>
    <h1>Ask a better question.</h1>
    <p>Start with an analytics question. Hyperset finds the governed context, shows how it connects, and keeps uncertainty visible.</p>
    <div className="shell-actions"><button className="debug-button primary" type="button" onClick={() => navigate("/playground/")}>Start a question</button></div>
    <div className="shell-home-grid">
      <button className="shell-card" type="button" onClick={() => navigate("/playground/explore/")}><span className="mini-label">Explore</span><b>Browse context</b><small>Find domains, concepts, and their source lineage.</small></button>
      <a className="shell-card" href={MCP_DOCS_URL}><span className="mini-label">Connect</span><b>MCP and docs</b><small>Connect a client to the same governed context surface.</small></a>
      <button className="shell-card" type="button" onClick={() => navigate("/playground/help/")}><span className="mini-label">Help</span><b>Learn the loop</b><small>See what Hyperset can resolve and what it will disclose.</small></button>
    </div>
  </section>;
}

function ExplorerDetail({ selection, bundle, nodeFilter, setNodeFilter, onStartChat, busy }) {
  const graph = bundle?.domain_graph || {};
  const nodes = Array.isArray(graph.nodes) ? graph.nodes : [];
  const edges = Array.isArray(graph.edges) ? graph.edges : [];
  const instructions = bundle?.instructions || {};
  const definitions = Array.isArray(instructions.definitions) ? instructions.definitions : [];
  const sources = Array.isArray(instructions.approved_sources) ? instructions.approved_sources : [];
  const authority = bundle?.context_authority || {};
  const resolution = bundle?.resolution || {};
  const status = String(resolution.status || "not_resolved").toLowerCase().replace(/_/g, "-");
  const needle = nodeFilter.trim().toLowerCase();
  const visibleNodes = nodes.filter((node) => !needle || [node.id, node.label, node.kind].some((value) => String(value || "").toLowerCase().includes(needle)));
  const labels = new Map(nodes.map((node) => [node.id, node.label || node.id]));
  const relatedEdges = edges.filter((edge) => !needle || [edge.from, edge.to, edge.relation].some((value) => String(value || "").toLowerCase().includes(needle)));
  const refinedQuestion = `Tell me about ${selection.concept || selection.domain}, including its approved sources, definitions, and relationships.`;
  return <div className="explorer-detail">
    <div className="explorer-detail-heading"><div><span className="mini-label">Selected context</span><h2>{selection.concept || selection.domain}</h2><p>{selection.concept ? `Concept in ${selection.domain}` : "Domain context"}</p></div><span className={`explorer-status ${status}`}>{resolution.status || "Select to resolve"}</span></div>
    {bundle ? <>
      <p className="explorer-summary">{resolution.summary || "This bundle is the exact governed response for the selected domain or concept."}</p>
      <div className="explorer-section explorer-graph-section"><div className="explorer-section-heading"><div><h3>Knowledge graph</h3><p>Relationships from the resolved ContextBundle.</p></div><span>{nodes.length} nodes · {edges.length} edges</span></div><DomainGraphView bundle={bundle} /></div>
      <div className="explorer-section"><div className="explorer-section-heading"><h3>Definitions</h3><span>{definitions.length}</span></div>{definitions.length ? definitions.map((definition, index) => <article className="explorer-definition" key={`${definition.term || "definition"}-${index}`}><b>{definition.term || "Definition"}</b><p>{definition.statement || "No statement supplied."}</p></article>) : <p className="explorer-muted">No governed definition was returned for this selection.</p>}</div>
      <div className="explorer-section"><div className="explorer-section-heading"><h3>Relationships</h3><span>{relatedEdges.length}</span></div><label className="explorer-filter">Filter nodes<input value={nodeFilter} onChange={(event) => setNodeFilter(event.target.value)} placeholder="Search node, kind, or relationship" /></label>{visibleNodes.length ? <div className="explorer-node-list">{visibleNodes.map((node) => <div className="explorer-node" key={node.id}><b>{node.label || node.id}</b><small>{node.kind || "node"} · {node.id}</small></div>)}</div> : <p className="explorer-muted">No nodes match this filter.</p>}{relatedEdges.length ? <div className="explorer-edge-list">{relatedEdges.map((edge, index) => <div className="explorer-edge" key={`${edge.from}-${edge.to}-${index}`}><b>{edge.relation || "related"}</b><small>{labels.get(edge.from) || edge.from} → {labels.get(edge.to) || edge.to}</small></div>)}</div> : <p className="explorer-muted">No relationships returned for this selection.</p>}</div>
      <div className="explorer-section"><div className="explorer-section-heading"><h3>Source and commit</h3></div><div className="explorer-meta"><span>Git path</span><code>{authority.path || "Not disclosed"}</code><span>Commit</span><code>{authority.commit_sha || "Not disclosed"}</code></div>{sources.length ? <div className="explorer-source-list">{sources.map((source, index) => <div className="explorer-source" key={`${source.ref || source}-${index}`}><b>{typeof source === "string" ? source : source.ref || "Source"}</b><small>{typeof source === "string" ? "approved source" : source.role || "approved source"}</small></div>)}</div> : <p className="explorer-muted">No approved source was returned.</p>}</div>
      <div className="shell-actions"><button className="debug-button primary" type="button" onClick={() => onStartChat(refinedQuestion)}>Ask about this context</button></div>
    </> : <div className="explorer-loading">{busy ? "Resolving the exact ContextBundle…" : "No ContextBundle was returned. Review the error above and try again."}</div>}
  </div>;
}

function ContextExplorer({ onStartChat }) {
  const [catalog, setCatalog] = useState(null);
  const [query, setQuery] = useState("");
  const [selection, setSelection] = useState(null);
  const [bundle, setBundle] = useState(null);
  const [nodeFilter, setNodeFilter] = useState("");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => { let mounted = true; requestJson("/v0/list_context_catalog", { limit: 200, offset: 0 }).then((data) => { if (mounted) setCatalog(data); }).catch((reason) => { if (mounted) setError(reason.message); }).finally(() => { if (mounted) setBusy(false); }); return () => { mounted = false; }; }, []);
  const entries = (catalog?.domains || []).flatMap((domain) => [{ domain: domain.domain, title: domain.title, concept: "", concepts: domain.concepts || [] }, ...(domain.concepts || []).map((concept) => ({ domain: domain.domain, title: domain.title, concept, concepts: [concept] }))]);
  const needle = query.trim().toLowerCase();
  const visibleEntries = entries.filter((entry) => !needle || [entry.domain, entry.title, entry.concept].some((value) => String(value || "").toLowerCase().includes(needle)));
  const select = async (entry) => {
    setSelection(entry); setBundle(null); setNodeFilter(""); setError(""); setBusy(true);
    try { setBundle(await requestJson("/v0/resolve_analytics_context", { query: `Tell me about ${entry.concept || entry.domain}.`, directive: { domains: [entry.domain], concepts: entry.concepts || [] } })); }
    catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  };
  return <section className="shell-page explorer-page">
    <div className="shell-page-heading"><div><span className="eyebrow">Explore context</span><h1>Find the governed pieces.</h1><p>Search the catalog, then inspect the exact definition, relationships, source, and Git commit behind a selection.</p></div><span className="explorer-count">{catalog ? `${visibleEntries.length} matches` : "Loading catalog…"}</span></div>
    <label className="explorer-search">Search domains, concepts, and nodes<input aria-label="Search domains and concepts" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Try revenue, churn, or a domain name" /></label>
    {error && <div className="error-banner">{error}</div>}
    <div className="explorer-layout"><div className="explorer-list" aria-label="Context catalog">{visibleEntries.length ? visibleEntries.map((entry, index) => <button type="button" className={`explorer-item ${selection?.domain === entry.domain && selection?.concept === entry.concept ? "selected" : ""}`} key={`${entry.domain}-${entry.concept || "domain"}-${index}`} onClick={() => select(entry)}><span className="explorer-item-kind">{entry.concept ? "Concept" : "Domain"}</span><b>{entry.concept || entry.domain}</b><small>{entry.concept ? `in ${entry.domain}` : entry.title || "Governed domain"}</small></button>) : <div className="explorer-empty">{busy ? "Loading the catalog…" : "No domains or concepts match this search."}</div>}</div>{selection && <ExplorerDetail selection={selection} bundle={bundle} nodeFilter={nodeFilter} setNodeFilter={setNodeFilter} onStartChat={onStartChat} busy={busy} />}</div>
  </section>;
}

function HiveMindPage() {
  return <section className="shell-page hive-page">
    <div className="hive-page-main"><HiveMindGraph requestJson={requestJson} /></div>
  </section>;
}

function PlaygroundTabs({ basePath }) {
  // The nine testing views collapse into one dropdown so they stop crowding the
  // primary surface nav (Playground · Review · Settings). Native <select> keeps
  // it accessible with no menu component.
  const navigate = useNavigate();
  const location = useLocation();
  const active = tabFromPath(location.pathname, basePath);
  return <label className="views-dropdown">
    <span>Views</span>
    <select value={active} onChange={(event) => navigate(tabPath(basePath, event.target.value))} aria-label="Playground testing views">
      {DEBUG_TABS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
    </select>
  </label>;
}

// The admin READINESS overview (hy-gh-75): a protected, read-only snapshot of whether
// each part of the deployment is operational. It fetches the authenticated admin
// endpoint (`/admin/api/v0/readiness`) and renders one row per component with a six-state
// badge plus last check, owner, impact, and recovery. It carries no secret value -- the
// backend never sends one -- and no control that changes state; it presents, only.
const READINESS_ORDER = { blocked: 0, degraded: 1, unknown: 2, disabled: 3, not_configured: 4, ready: 5 };

function ReadinessPanel() {
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  const refresh = React.useCallback(() => {
    setLoading(true);
    requestJson("/v0/readiness", null, "GET")
      .then((data) => { setReport(data); setError(null); })
      .catch((err) => setError(err.message || "failed to load readiness"))
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => { refresh(); }, [refresh]);

  const components = report?.components
    ? [...report.components].sort((a, b) => (READINESS_ORDER[a.status] ?? 9) - (READINESS_ORDER[b.status] ?? 9))
    : [];
  return (
    <section className="readiness-panel">
      <div className="readiness-head">
        <h2>Readiness</h2>
        {report?.overall && <span className={`status-badge ${report.overall}`}>{report.overall}</span>}
        <button type="button" className="readiness-refresh" onClick={refresh} disabled={loading}>
          {loading ? "Checking…" : "Refresh"}
        </button>
      </div>
      {error && <p className="readiness-error">Could not load readiness — {error}</p>}
      {!error && components.length === 0 && !loading && <p className="empty-debug">No components reported.</p>}
      <div className="readiness-grid">
        {components.map((c) => (
          <article className="readiness-row" key={c.component}>
            <div className="readiness-row-head">
              <b>{c.component}</b>
              <span className={`status-badge ${c.status}`}>{c.status}</span>
            </div>
            {c.detail && <p className="readiness-detail">{c.detail}</p>}
            <dl className="readiness-meta">
              <div><dt>Last check</dt><dd>{c.checked_at ? new Date(c.checked_at).toLocaleString() : "never"}</dd></div>
              <div><dt>Owner</dt><dd>{c.owner}</dd></div>
              <div><dt>Impact</dt><dd>{c.impact}</dd></div>
              <div><dt>Recovery</dt><dd>{c.recovery}</dd></div>
            </dl>
          </article>
        ))}
      </div>
    </section>
  );
}

// Admin CONTEXT SOURCE management (hy-gh-75): list the configured Git context sources with
// their serving commit / last sync / validation state, add a repo/ref/path source, and sync
// (validate + fetch) one. It manages the Git POINTER only (ADR-0012) -- it creates no
// governed meaning and approves nothing. Authenticated admin endpoints; no secret is shown.
// Admin AUDIT TRAIL (hy-gh-75): a read-only, newest-first table of admin config actions
// (who / what / target / when / result). Monitoring only -- it changes nothing.
function AuditPanel() {
  const [entries, setEntries] = useState([]);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  const refresh = React.useCallback(() => {
    setLoading(true);
    requestJson("/v0/audit", null, "GET")
      .then((data) => { setEntries(data.entries || []); setError(null); })
      .catch((err) => setError(err.message || "failed to load audit trail"))
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => { refresh(); }, [refresh]);

  return (
    <section className="audit-panel">
      <div className="readiness-head">
        <h2>Audit trail</h2>
        <button type="button" className="readiness-refresh" onClick={refresh} disabled={loading}>{loading ? "Loading…" : "Refresh"}</button>
      </div>
      {error && <p className="readiness-error">{error}</p>}
      {!error && entries.length === 0 && !loading && <p className="empty-debug">No admin actions recorded yet.</p>}
      {entries.length > 0 && (
        <table className="audit-table">
          <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Target</th><th>Result</th></tr></thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.id}>
                <td>{e.at ? new Date(e.at).toLocaleString() : "—"}</td>
                <td>{e.actor}</td>
                <td>{e.action}</td>
                <td>{e.target || "—"}</td>
                <td><span className={`status-badge ${e.result === "ok" ? "ready" : "blocked"}`}>{e.result}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}


function SettingsPanel({ runtimeConfig }) {
  // The admin surface is SETTINGS/CONFIG only (hy-0clf): the write-back target,
  // and a summary of the deployment's connections, agents, and models. It holds
  // no chat, no review workflow, and no diagnostics -- those live on the public
  // playground. Setting the target goes through the admin api prefix (API_ROOT),
  // which the backend serves only off the admin surface.
  const [writeback, setWriteback] = useState(null);
  const [draft, setDraft] = useState({ repository: "", base_ref: "main", manifest_path: "", token_source: "env_ref", token_ref: "", token: "", app_id: "", app_private_key: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const loadWriteback = async () => { try { const data = await requestJson("/v0/review/writeback-config", null, "GET"); setWriteback(data.config || null); if (data.config) setDraft({ repository: data.config.repository, base_ref: data.config.base_ref, manifest_path: data.config.manifest_path, token_source: data.config.token_source || "env_ref", token_ref: data.config.token_ref || "", token: "", app_id: data.config.app_id == null ? "" : String(data.config.app_id), app_private_key: "" }); } catch (reason) { setError(reason.message); } };
  useEffect(() => { if (writeback === null) loadWriteback(); }, [writeback]);
  const save = async () => {
    setBusy(true); setError("");
    // The pasted token and App private key are WRITE-ONLY: sent only when
    // re-entered, never read back.
    const payload = { repository: draft.repository, base_ref: draft.base_ref, manifest_path: draft.manifest_path, token_source: draft.token_source };
    if (draft.token_source === "env_ref") payload.token_ref = draft.token_ref;
    else if (draft.token_source === "encrypted") { if (draft.token) payload.token = draft.token; }
    else { payload.app_id = draft.app_id; if (draft.app_private_key) payload.app_private_key = draft.app_private_key; }
    try { const data = await requestJson("/v0/review/writeback-config", payload); setWriteback(data.config); setDraft((current) => ({ ...current, token: "", app_private_key: "" })); }
    catch (reason) { setError(reason.message); } finally { setBusy(false); }
  };
  const summarize = (items, defaultValue) => (items || []).map((item) => `${item.label}${item.value === defaultValue ? " · default" : ""}`).join(" · ") || "—";
  const isUrlTarget = /^(https?|git|ssh):\/\/|^git@/.test(draft.repository.trim());
  const encryptedSet = writeback && writeback.token_source === "encrypted" && writeback.token_set;
  const appKeySet = writeback && writeback.token_source === "github_app" && writeback.token_set;
  return <section className="debug-panel">
    <div className="debug-panel-heading"><div><span className="eyebrow">Admin settings</span><h2>Configuration</h2></div><span className="debug-status">{busy ? "working…" : "admin only"}</span></div>
    {error && <div className="error-banner">{error}</div>}
    <div className="writeback-config">
      <div className="review-label">Write-back repository — the proposal target for the public review surface</div>
      <div className="debug-form">
        <label>Repository (URL or local path)<input value={draft.repository} onChange={(event) => setDraft((current) => ({ ...current, repository: event.target.value }))} placeholder="https://github.com/org/context-repo or /path/to/repo" /></label>
        <label>Base ref<input value={draft.base_ref} onChange={(event) => setDraft((current) => ({ ...current, base_ref: event.target.value }))} placeholder="main" /></label>
        <label>Manifest path<input value={draft.manifest_path} onChange={(event) => setDraft((current) => ({ ...current, manifest_path: event.target.value }))} placeholder="domains/revenue" /></label>
        <label>Token source (for a URL target)<select value={draft.token_source} onChange={(event) => setDraft((current) => ({ ...current, token_source: event.target.value }))}><option value="github_app">GitHub App (enterprise default · short-lived tokens)</option><option value="env_ref">Env var name (external secret manager)</option><option value="encrypted">Paste token (stored encrypted)</option></select></label>
        {draft.token_source === "env_ref"
          ? <label>Token reference (server-side env var NAME)<input value={draft.token_ref} onChange={(event) => setDraft((current) => ({ ...current, token_ref: event.target.value }))} placeholder="HYPERSET_WRITEBACK_TOKEN" /></label>
          : draft.token_source === "encrypted"
          ? <label>GitHub token (write-only, stored encrypted)<input type="password" autoComplete="off" value={draft.token} onChange={(event) => setDraft((current) => ({ ...current, token: event.target.value }))} placeholder={encryptedSet ? "•••• set · encrypted (leave blank to keep)" : "paste a GitHub token"} /></label>
          : <>
              <label>GitHub App ID<input value={draft.app_id} onChange={(event) => setDraft((current) => ({ ...current, app_id: event.target.value }))} placeholder="123456" /></label>
              <label>App private key (.pem, write-only, stored encrypted)<textarea autoComplete="off" value={draft.app_private_key} onChange={(event) => setDraft((current) => ({ ...current, app_private_key: event.target.value }))} placeholder={appKeySet ? "•••• set · encrypted (leave blank to keep)" : "paste the App private key .pem"} rows={4} /></label>
            </>}
      </div>
      <div className="debug-actions"><button className="debug-button primary" disabled={busy || !draft.repository.trim() || !draft.manifest_path.trim()} onClick={save}>Save write-back target</button></div>
      <small className="candidate-signal">{writeback ? `Configured: ${writeback.repository} @ ${writeback.base_ref} · ${writeback.manifest_path} · token ${writeback.token_source}${writeback.token_set ? " · set" : " · none"}${writeback.token_source === "env_ref" && writeback.token_ref ? ` (${writeback.token_ref})` : ""}.` : "No write-back repository configured — set one to enable Propose to Git on the public review surface."} {isUrlTarget ? (draft.token_source === "github_app" ? "GitHub App (enterprise default): paste the App ID and private key .pem. The private key is encrypted (AES-256-GCM) server-side — never returned to the browser, never pre-filled. At each propose the server signs a short-lived JWT and mints a per-operation installation token that is never stored; writes appear as hyperset[bot]." : draft.token_source === "encrypted" ? "Paste a GitHub token: it is encrypted (AES-256-GCM) server-side and stored as ciphertext — never returned to the browser, never pre-filled. The key that decrypts it lives only in the server environment." : "Enter the NAME of a server-side secret (env var), never the token itself — the raw token is read from the server environment at propose time and is never sent to the browser.") : "A local path works with zero secrets."} This configures the target only; it never approves or merges (ADR 0012).</small>
    </div>
    <div className="review-row"><span className="review-label">Connections</span><span>{summarize(runtimeConfig.connections)}</span></div>
    <div className="review-row"><span className="review-label">Agents</span><span>{summarize(runtimeConfig.agents, runtimeConfig.default_agent)}</span></div>
    <div className="review-row"><span className="review-label">Models</span><span>{summarize(runtimeConfig.models, runtimeConfig.default_model)}</span></div>
    <small className="candidate-signal">Connections, agents, and models are configured per deployment (environment); this is a read-only summary.</small>
  </section>;
}

function SettingsAccordion({ title, copy, badge, children, open = false }) {
  return <details className="settings-accordion" open={open}>
    <summary><span><b>{title}</b>{copy && <small>{copy}</small>}</span>{badge && <em>{badge}</em>}</summary>
    <div className="settings-accordion-body">{children}</div>
  </details>;
}

function AdminSettingsPage({ runtimeConfig }) {
  const authEnabled = Boolean(runtimeConfig.auth?.enabled);
  return <section className="settings-page">
    <div className="settings-page-heading">
      <div><span className="eyebrow">Admin workspace</span><h1>Settings</h1><p>One place to connect Claude, inspect deployment health, manage context sources, and control the governed write-back path.</p></div>
      <span className="settings-lock">Admin only</span>
    </div>
    <div className="settings-overview">
      <ReadinessPanel />
      <article className="settings-auth-card">
        <div className="settings-card-heading"><div><span className="mini-label">Identity</span><h2>Authentication</h2></div><span className={`status-badge ${authEnabled ? "ready" : "unknown"}`}>{authEnabled ? "OIDC enabled" : "Local demo"}</span></div>
        <p>{authEnabled ? "Users sign in through the configured OIDC provider before accessing protected admin and review actions." : "OIDC is disabled for this loopback deployment. The login endpoint remains available when authentication is enabled."}</p>
        <a className="settings-link" href={`/login?return=${encodeURIComponent("/admin/")}`}>{authEnabled ? "Test login ↗" : "View login endpoint ↗"}</a>
      </article>
    </div>
    <SettingsAccordion title="Connect Claude via MCP" copy="Configure a client and run a bounded discover → resolve check." badge="MCP" open>
      <div id="mcp"><McpSetupWizard /></div>
    </SettingsAccordion>
    <SettingsAccordion title="Write-back and proposal routing" copy="Configure where reviewer proposals go; nothing is merged automatically here." badge="Governed" open>
      <SettingsPanel runtimeConfig={runtimeConfig} />
      <div className="settings-subsection"><WritebackTargetsPanel requestJson={requestJson} /></div>
    </SettingsAccordion>
    <SettingsAccordion title="Context sources and connections" copy="Manage synced Git pointers and inspect connected systems." badge="Sources">
      <div className="settings-two-column"><ContextSourcesPanel requestJson={requestJson} /><ConnectionsPanel requestJson={requestJson} /></div>
    </SettingsAccordion>
    <SettingsAccordion title="Audit and diagnostics" copy="Operational history for troubleshooting the feedback loop." badge="Ops">
      <AuditPanel />
      <DiagnosticsPanel requestJson={requestJson} />
    </SettingsAccordion>
  </section>;
}

export function ReviewPage({ request = requestJson } = {}) {
  // The dedicated reviewer surface (hy-1f96): readable cards, inline edit and
  // refine, and Propose to Git returning a clickable PR link. It consumes the
  // review HTTP endpoints already served; it holds no approval and opens only a
  // proposal PR (ADR 0012). Raw JSON is behind an explicit developer toggle.
  // `request` is injectable so a test drives it without a live backend.
  const [tasks, setTasks] = useState(null);
  const [writeback, setWriteback] = useState(null);
  // WHO 'me' is, for the owner-on-card and 'assigned to me' filter (hy-q7pth
  // round 2): the CURRENT VERIFIED PRINCIPAL as the SERVER sees it, read fresh
  // from /v0/review/whoami. NOT persisted -- a stale login, an anonymous (null)
  // caller, or edited client storage can never forge 'mine'. Empty until whoami
  // resolves, and empty for an anonymous/unverified caller, which disables the
  // filter rather than treating every anonymous-owned task as mine.
  const [myIdentity, setMyIdentity] = useState("");
  // Reviewer-queue filters (hy-iomc), all client-side over the served list.
  const [mineOnly, setMineOnly] = useState(false);
  const [unassignedOnly, setUnassignedOnly] = useState(false);
  const [staleOnly, setStaleOnly] = useState(false);
  const [statusFilter, setStatusFilter] = useState("");
  const [priorityFilter, setPriorityFilter] = useState("");
  // Proposal result is per task, and the backend does not persist a "proposed"
  // status, so localStorage keeps the confirmation + PR link across a refresh.
  const [proposedByTask, setProposedByTask] = useState(() => { try { return JSON.parse(localStorage.getItem("hyperset-proposed") || "{}"); } catch { return {}; } });
  const [errorByTask, setErrorByTask] = useState({});
  // The human include/exclude/approve/reject decisions recorded this session, per task and
  // citation ref, so the card can show what the reviewer just decided (the durable row is
  // written server-side by /v0/review/citations/decide; this mirrors the returned decision).
  const [decisionsByTask, setDecisionsByTask] = useState({});
  const [decidingKey, setDecidingKey] = useState("");
  const [proposingId, setProposingId] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [showJson, setShowJson] = useState(false);
  const persistProposed = (next) => { setProposedByTask(next); try { localStorage.setItem("hyperset-proposed", JSON.stringify(next)); } catch { /* ignore quota */ } };
  const loadReview = async () => { setLoading(true); setError(""); try { setTasks(await request("/v0/list_review_tasks", {})); } catch (reason) { setError(reason.message); } finally { setLoading(false); } };
  const loadWriteback = async () => { try { const data = await request("/v0/review/writeback-config", null, "GET"); setWriteback(data.config || null); } catch (reason) { setError(reason.message); } };
  // The SOLE identity authority: the caller's OWN server-derived opaque identity,
  // read fresh (never persisted). null (anonymous/unverified) leaves 'me' empty.
  const loadWhoami = async () => { try { const data = await request("/v0/review/whoami", null, "GET"); setMyIdentity(redactDeep(data.identity) || ""); } catch { setMyIdentity(""); } };
  useEffect(() => { loadReview(); loadWriteback(); loadWhoami(); }, []);
  const propose = async (taskId) => { setProposingId(taskId); setErrorByTask((e) => ({ ...e, [taskId]: "" })); try { const data = await request("/v0/propose_review_to_git", { task_id: taskId }); persistProposed({ ...proposedByTask, [taskId]: data.proposal }); await loadReview(); } catch (reason) { setErrorByTask((e) => ({ ...e, [taskId]: reason.message })); } finally { setProposingId(null); } };
  const undoPropose = (taskId) => { const next = { ...proposedByTask }; delete next[taskId]; persistProposed(next); };
  const previewTask = (taskId) => request(`/v0/review/tasks/preview?task_id=${encodeURIComponent(taskId)}`, null, "GET");
  const refine = async (taskId, feedback) => { setBusy(true); setError(""); try { await request("/v0/refine_review_draft", { task_id: taskId, feedback }); await loadReview(); } catch (reason) { setError(reason.message); } finally { setBusy(false); } };
  const requestEvidence = async (taskId) => { setBusy(true); setError(""); try { await request("/v0/review/tasks/request-evidence", { task_id: taskId }); await loadReview(); } catch (reason) { setError(reason.message); } finally { setBusy(false); } };
  const editDraft = async (taskId, definition) => { setBusy(true); setError(""); try { await request("/v0/edit_review_draft", { task_id: taskId, definition }); await loadReview(); } catch (reason) { setError(reason.message); } finally { setBusy(false); } };
  // Self-claim: the server assigns the task to THIS caller's computed identity.
  // 'me' comes from whoami (the same server-side identity), never from this
  // response, so the queue reload alone reflects the new ownership.
  const assignSelf = async (taskId) => { setBusy(true); setError(""); try { await request("/v0/set_review_assignee", { task_id: taskId, assigned: true }); await loadReview(); } catch (reason) { setError(reason.message); } finally { setBusy(false); } };
  const unassign = async (taskId) => { setBusy(true); setError(""); try { await request("/v0/set_review_assignee", { task_id: taskId, assigned: false }); await loadReview(); } catch (reason) { setError(reason.message); } finally { setBusy(false); } };
  // Record a human include/exclude/approve/reject on ONE cited source of a task. Writes a
  // durable citation_decisions row (linked to the task by review_task_id) via the served
  // /v0/review/citations/decide route; the returned decision is mirrored onto the card so the
  // reviewer sees the outcome. It records only an audit decision — it approves/merges nothing
  // and advances no status (ADR 0012).
  const decide = async (taskId, citationRef, sourceRef, decision) => {
    const key = `${taskId}::${citationRef}`;
    setDecidingKey(key); setError("");
    try {
      const data = await request("/v0/review/citations/decide", { review_task_id: taskId, citation_ref: citationRef, source_ref: sourceRef, decision });
      setDecisionsByTask((cur) => ({ ...cur, [taskId]: { ...(cur[taskId] || {}), [citationRef]: data.decision } }));
    } catch (reason) { setError(reason.message); } finally { setDecidingKey(""); }
  };
  const allTasks = tasks?.tasks || [];
  const openCount = allTasks.filter((t) => !proposedByTask[t.id]).length;
  const repoName = writeback ? (writeback.repository || "").split("/").slice(-2).join("/") || writeback.repository : null;
  // The legacy read returns only the optional default target. Proposal routing itself is
  // domain-aware (keyed target first, default second) and fail-closed in the served operation,
  // so a missing default must not disable a valid per-domain target in the Review UI.
  const canPropose = !loading && tasks !== null;
  const filters = { status: statusFilter, priority: priorityFilter, mineOnly, unassignedOnly, staleOnly };
  const priorities = [...new Set(allTasks.map((t) => t.priority).filter((priority) => priority !== null && priority !== undefined && priority !== "").map(String))].sort((a, b) => Number(a) - Number(b));
  const statuses = ["open", "in_progress", "resolved", "dismissed"];
  return <section className="review-page">
    <div className="review-page-heading">
      <div className="review-page-intro">
        <span className="eyebrow">Reviewer</span>
        <h1>Context review</h1>
        <p className="review-lede">Confirm a drafted definition, then propose it to Git — a proposal-only PR a human merges (ADR 0012). Nothing here approves, merges, or writes governed authority.</p>
      </div>
      <div className="review-page-actions" role="group" aria-label="Review queue filters">
        <label className="review-filter" title="Filter by task status"><span>Status</span>
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="">All</option>
            {statuses.map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
          </select>
        </label>
        <label className="review-filter" title="Filter by urgency (priority)"><span>Urgency</span>
          <select value={priorityFilter} onChange={(event) => setPriorityFilter(event.target.value)}>
            <option value="">All</option>
            {priorities.map((p) => <option key={p} value={String(p)}>{`Priority ${p}`}</option>)}
          </select>
        </label>
        <label className="review-mine-toggle" title={myIdentity ? "Show only the tasks assigned to you" : "Sign in to filter by your tasks"}><input type="checkbox" checked={mineOnly} disabled={!myIdentity} onChange={(event) => setMineOnly(event.target.checked)} /> Assigned to me</label>
        <label className="review-mine-toggle" title="Show only unassigned gaps you can claim"><input type="checkbox" checked={unassignedOnly} onChange={(event) => setUnassignedOnly(event.target.checked)} /> Unassigned</label>
        <label className="review-mine-toggle" title="Show only tasks awaiting review for over a week"><input type="checkbox" checked={staleOnly} onChange={(event) => setStaleOnly(event.target.checked)} /> Stale</label>
        <button className="debug-button" disabled={busy || loading} onClick={loadReview}>{loading ? "Refreshing…" : "Refresh"}</button>
      </div>
    </div>
    {!loading && allTasks.length > 0 && <div className="review-queue-head">
      <span className="review-count">{openCount === 0 ? "All proposed" : `${openCount} gap${openCount > 1 ? "s" : ""} waiting`}</span>
      {writeback ? <span className="review-repo-chip ok" title={writeback.repository}>Writing to {repoName} ✓</span> : <span className="review-repo-chip pending">Routing target checked per domain · <a href="/admin/">Settings</a></span>}
    </div>}
    {error && <div className="error-banner">{error} <button type="button" className="linklike" onClick={() => { setError(""); loadReview(); }}>Try again</button></div>}
    {loading
      ? <div className="review-loading">Loading review queue…</div>
      : <ReviewTaskList data={tasks} onPropose={propose} onEdit={editDraft} onPreview={previewTask} onRefine={refine} onRequestEvidence={requestEvidence} busy={busy} canPropose={canPropose} writeback={writeback} proposedByTask={proposedByTask} errorByTask={errorByTask} proposingId={proposingId} onUndo={undoPropose} myIdentity={myIdentity} onAssignSelf={assignSelf} onUnassign={unassign} filters={filters} onDecide={decide} decisionsByTask={decisionsByTask} decidingKey={decidingKey} />}
    <div className="review-devtools">
      <label className="review-json-toggle"><input type="checkbox" checked={showJson} onChange={(event) => setShowJson(event.target.checked)} /> Developer view (raw JSON)</label>
      {showJson && <DebugJson value={tasks} empty="No review tasks loaded." />}
    </div>
  </section>;
}

function App({ admin, review, activeTab, adminTab = "readiness", basePath, userPage = "", prefillQuestion = "", restore = null }) {
  const [theme, setTheme] = useState(() => localStorage.getItem("hyperset-theme") || "light");
  const [runtimeConfig, setRuntimeConfig] = useState({ agents: [], models: [], connections: [], tools: [], default_agent: "", default_model: "", auth: { enabled: false } });
  const [agent, setAgent] = useState("");
  const [model, setModel] = useState("");
  const [backendHealthy, setBackendHealthy] = useState(false);
  const [debugBundle, setDebugBundle] = useState(null);
  const [apiVersion, setApiVersion] = useState("…");
  const publicDebug = !admin && !review && !userPage && activeTab !== "chat";
  const userShell = !admin && !review;
  const userSection = userPage || "chat";
  const navigate = useNavigate();
  const surface = admin ? "admin" : review ? "review" : "playground";
  const restoredMessages = Array.isArray(restore?.messages) ? restore.messages : [];
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem("hyperset-theme", theme); }, [theme]);
  useEffect(() => {
    document.body.classList.toggle("playground-page", !admin && !review && !publicDebug && !userPage);
    document.body.classList.toggle("playground-debug-page", publicDebug || review || !!userPage);
    return () => {
      document.body.classList.remove("playground-page");
      document.body.classList.remove("playground-debug-page");
    };
  }, [admin, review, publicDebug, userPage]);
  useEffect(() => {
    let mounted = true;
    const loadRuntime = async () => {
      try {
        const data = await requestJson("/v0/playground/status", null, "GET");
        if (!mounted) return;
        const config = data.playground || {};
        const agents = Array.isArray(config.agents) ? config.agents : [];
        const models = Array.isArray(config.models) ? config.models : [];
        setRuntimeConfig({ agents, models, connections: Array.isArray(config.connections) ? config.connections : [], tools: Array.isArray(config.tools) ? config.tools : [], default_agent: config.default_agent || "", default_model: config.default_model || "", auth: data.auth || { enabled: false } });
        setAgent((current) => agents.some((item) => item.value === current) ? current : config.default_agent || agents[0]?.value || "");
        setModel((current) => models.some((item) => item.value === current) ? current : config.default_model || models[0]?.value || "");
        setApiVersion(data.hyperset?.version || "unknown");
        setBackendHealthy(data.hyperset?.status === "ok" || data.hyperset?.status === "connected");
      } catch {
        if (!mounted) return;
        setBackendHealthy(false);
        setApiVersion("unknown");
      }
    };
    loadRuntime();
    const interval = window.setInterval(loadRuntime, 15000);
    return () => { mounted = false; window.clearInterval(interval); };
  }, []);
  // Restore a reopened thread's agent/model (hy-87n1). The run mode is passed as
  // HypersetChat's initial state below. The runtime loader keeps a valid current
  // selection, so setting a restored (valid) agent/model here is not clobbered by it; a
  // stale selection falls back to the deployment default. Applied once per restore.
  useEffect(() => {
    if (restore?.agent) setAgent(restore.agent);
    if (restore?.model) setModel(restore.model);
  }, [restore?.agent, restore?.model]);
  return (
    <div className={`app ${surface}-app ${publicDebug || userPage ? "public-debug-app" : ""}`}>
      <Header surface={surface} theme={theme} onThemeChange={setTheme} userShell={userShell} userSection={userSection} auth={runtimeConfig.auth} />
      {admin && <div className="admin-ribbon"><span>ADMIN · SETTINGS</span></div>}
      <main className={`page-shell ${publicDebug || review || userPage ? "public-debug-shell" : "public-chat-shell"}`}>
        {admin ? <div className="admin-main">
          {adminTab === "readiness" && <AdminSettingsPage runtimeConfig={runtimeConfig} />}
          {adminTab === "connections" && <ConnectionsPanel requestJson={requestJson} />}
          {adminTab === "sources" && <ContextSourcesPanel requestJson={requestJson} />}
          {adminTab === "audit" && <AuditPanel />}
          {adminTab === "diagnostics" && <DiagnosticsPanel requestJson={requestJson} />}
          {adminTab === "configuration" && <SettingsPanel runtimeConfig={runtimeConfig} />}
          {adminTab === "writeback" && <WritebackTargetsPanel requestJson={requestJson} />}
        </div>
          : review ? <div className="review-main"><ReviewPage /></div>
          : <div className={`public-main ${publicDebug || userPage ? "public-debug-main" : "public-chat-main"}`}>
            {publicDebug ? <AdminDebugPanel active={activeTab} model={model} models={runtimeConfig.models} agents={runtimeConfig.agents} connections={runtimeConfig.connections} tools={runtimeConfig.tools} defaultAgent={runtimeConfig.default_agent} bundle={debugBundle} setBundle={setDebugBundle} publicView />
              : userPage === "explore" ? <HiveMindPage />
              : <LiveChatPage apiRoot={API_ROOT} agent={agent} model={model} models={runtimeConfig.models} agents={runtimeConfig.agents} backendHealthy={backendHealthy} setAgent={setAgent} setModel={setModel} initialQuestion={restoredMessages.length ? "" : prefillQuestion} initialMessages={restoredMessages} initialGovernedOnly={restore ? restore.governedOnly !== false : true} />}
          </div>}
      </main>
      {(admin || review || publicDebug || userPage) && <footer className="site-footer"><span>Hyperset API · {apiVersion}</span></footer>}
    </div>
  );
}

function RoutedApp() {
  const location = useLocation();
  const navigate = useNavigate();
  const admin = isAdmin();
  const review = isReview();
  // Admin and review are single pages; public shell routes and debug tabs all
  // canonicalize to trailing-slash paths. The shell stays client-side so the
  // HTTP adapter can serve every route from the same built index.
  const basePath = admin ? "/admin" : review ? "/review" : "/playground";
  const adminTab = admin ? adminTabFromPath(location.pathname) : "readiness";
  const userPage = !admin && !review ? userPageFromPath(location.pathname, basePath) : "";
  const activeTab = admin || review || userPage ? "chat" : tabFromPath(location.pathname, basePath);
  const canonicalPath = admin ? adminTabPath(adminTab) : review ? `${basePath}/` : userPage ? `${basePath}/${userPage}/` : tabPath(basePath, activeTab);

  useEffect(() => {
    if (location.pathname !== canonicalPath) navigate(canonicalPath, { replace: true });
  }, [canonicalPath, location.pathname, navigate]);

  // A reopened Recent thread hands its state through localStorage (hy-87n1); read it once
  // on mount so a reopen restores the question AND its run settings, and a plain refresh
  // does not re-restore.
  const [restore] = useState(() => readThreadRestore(localStorage));
  const prefillQuestion = restore?.question || new URLSearchParams(location.search).get("question") || (typeof location.state?.question === "string" ? location.state.question : "");
  return <App admin={admin} review={review} activeTab={activeTab} adminTab={adminTab} basePath={basePath} userPage={userPage} prefillQuestion={prefillQuestion} restore={restore} />;
}

// Mount only when the app's root element is present (the served page). Guarding it
// lets a test import components from this module without the top-level render firing
// against a missing container.
const _rootEl = document.getElementById("root");
if (_rootEl) createRoot(_rootEl).render(<BrowserRouter><RoutedApp /></BrowserRouter>);
