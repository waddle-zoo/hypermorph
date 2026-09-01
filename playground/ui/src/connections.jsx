import React, { useCallback, useEffect, useState } from "react";

// Admin Connections / Observed Sources (hq-xptv): manage the Superset/DataHub
// observed-evidence connections served by the slice-6 admin endpoints
// (list/add/update/enable-disable/remove/probe). It lists each connection's
// type, display name, config reference (a base URL / bundle path -- NEVER a
// secret; the credential lives only in the server environment), enabled state,
// and RECORDED health (readiness). A "Probe" runs a bounded LIVE check and shows
// configured/reachable/fresh + reason/impact/recovery. GREEN LIVENESS IS NOT
// READINESS: a reachable but never/stale-synced source shows DEGRADED, and an
// unreachable or unconfigured one BLOCKED -- surfaced honestly, not as green.
// Remove is pre-disabled for a connection that holds observed assets (disable
// instead -- observed evidence keeps its source identity). The server CONFIGURE
// gate is the real boundary. Any free-text error is already server-redacted.

const CONNECTOR_TYPES = ["superset", "datahub"];
const EMPTY_FORM = { connector_type: "superset", display_name: "", config_ref: "" };

// Defense-in-depth on the render boundary: redact URL userinfo from the free-text
// fields the server sends (config_ref, health_detail, probe reason). It is the
// EXACT canonical `scheme://userinfo@` rule the server uses (#431) -- redact
// `https://user:token@host` (the `@` before the first `/`), while a port + git
// revision (`https://git.corp:8443/...HEAD@{1}`, `@` after the first `/`) and an
// scp `git@host:path` (no scheme) are preserved. The server already redacts these
// fields (that is the boundary); this only guarantees the UI cannot render a
// credential a field somehow carried. Pure string replace -- never throws.
const URL_USERINFO_G = /([a-zA-Z][a-zA-Z0-9+.\-]*:\/\/)[^/]*@/g;

export function redactUserinfo(value) {
  return typeof value === "string" ? value.replace(URL_USERINFO_G, "$1") : value;
}

// The recorded health_status (readiness) and the live probe status map to the
// same badge palette so the two read consistently.
function healthClass(status) {
  return status === "healthy" || status === "ready"
    ? "ready"
    : status === "degraded" || status === "unknown"
      ? "unknown"
      : "blocked";
}

export function ConnectionsPanel({ requestJson }) {
  const [connections, setConnections] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [editingId, setEditingId] = useState(null);
  const [probeByConnection, setProbeByConnection] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(() => {
    requestJson("/v0/connections", null, "GET")
      .then((data) => { setConnections(data.connections || []); setError(""); })
      .catch((reason) => setError(reason.message));
  }, [requestJson]);
  useEffect(() => { refresh(); }, [refresh]);

  const save = (event) => {
    event.preventDefault();
    if (!form.display_name.trim() || !form.config_ref.trim()) {
      setError("a connection needs a display name and a config reference (base URL or bundle path)");
      return;
    }
    setBusy(true); setError("");
    const payload = {
      connector_type: form.connector_type,
      display_name: form.display_name.trim(),
      config_ref: form.config_ref.trim(),
    };
    if (editingId !== null) payload.id = editingId;
    requestJson("/v0/connections", payload, "POST")
      .then(() => { setForm(EMPTY_FORM); setEditingId(null); refresh(); })
      .catch((reason) => setError(reason.message))
      .finally(() => setBusy(false));
  };

  const edit = (c) => {
    setEditingId(c.id);
    setForm({ connector_type: c.connector_type, display_name: c.display_name, config_ref: c.config_ref || "" });
  };
  const cancelEdit = () => { setEditingId(null); setForm(EMPTY_FORM); };

  const setEnabled = (c, enabled) => {
    setBusy(true); setError("");
    requestJson("/v0/connections/enable", { id: c.id, enabled }, "POST")
      .then(refresh).catch((reason) => setError(reason.message)).finally(() => setBusy(false));
  };
  const remove = (c) => {
    setBusy(true); setError("");
    requestJson("/v0/connections/remove", { id: c.id }, "POST")
      .then(refresh).catch((reason) => setError(reason.message)).finally(() => setBusy(false));
  };
  const probe = (c) => {
    setBusy(true); setError("");
    requestJson("/v0/connections/probe", { id: c.id }, "POST")
      .then((data) => setProbeByConnection((prev) => ({ ...prev, [c.id]: data.probe })))
      .catch((reason) => setError(reason.message)).finally(() => setBusy(false));
  };

  return (
    <section className="connections-panel">
      <h2>Connections / observed sources</h2>
      <p className="sources-note">
        Superset and DataHub observed-evidence connections. A connection stores a base URL or
        bundle path only -- credentials live in the server environment, never here. RECORDED
        health is readiness; a live Probe reports configured/reachable/fresh honestly (a
        reachable but stale source is degraded, not green). Observed evidence keeps its source
        identity: a connection with observed assets is disable-only.
      </p>
      {error && <p className="readiness-error" role="alert">{error}</p>}
      <form className="sources-add" onSubmit={save}>
        <select aria-label="connector type" value={form.connector_type}
          onChange={(e) => setForm({ ...form, connector_type: e.target.value })}>
          {CONNECTOR_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <input aria-label="display name" placeholder="display name" value={form.display_name}
          onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
        <input aria-label="config ref" placeholder="base URL or bundle path" value={form.config_ref}
          onChange={(e) => setForm({ ...form, config_ref: e.target.value })} />
        <button type="submit" disabled={busy}>{editingId !== null ? "Update connection" : "Add connection"}</button>
        {editingId !== null && <button type="button" onClick={cancelEdit} disabled={busy}>Cancel</button>}
      </form>
      <div className="sources-grid">
        {connections === null && <p className="empty-debug">Loading…</p>}
        {connections !== null && connections.length === 0 && <p className="empty-debug">No connections configured.</p>}
        {(connections || []).map((c) => {
          const p = probeByConnection[c.id];
          return (
            <article className="sources-row" key={c.id}>
              <div className="sources-row-head">
                <b>{c.connector_type} · {c.display_name}</b>
                <span className={`status-badge ${c.enabled ? "ready" : "unknown"}`}>{c.enabled ? "enabled" : "disabled"}</span>
                <span className={`status-badge ${healthClass(c.health_status)}`} title="recorded health (readiness)">
                  {c.health_status}
                </span>
              </div>
              <dl className="readiness-meta">
                <div><dt>Connection ID</dt><dd>{c.id}</dd></div>
                <div><dt>Config reference</dt><dd>{redactUserinfo(c.config_ref) || "—"}</dd></div>
                <div><dt>Health checked</dt><dd>{c.health_checked_at ? new Date(c.health_checked_at).toLocaleString() : "never"}</dd></div>
                {c.health_detail && <div><dt>Health detail</dt><dd>{redactUserinfo(c.health_detail)}</dd></div>}
              </dl>
              {p && (
                <div className={`status-badge probe-result ${healthClass(p.status)}`} role="status">
                  <b>{p.status}</b>{p.reason ? ` — ${redactUserinfo(p.reason)}` : ""}
                  {/* The live facts, so liveness is observably distinct from the recorded
                      health badge (green liveness is not readiness). */}
                  <div className="probe-facts">
                    configured: {p.configured ? "yes" : "no"} · reachable: {p.reachable ? "yes" : "no"} · fresh: {p.fresh ? "yes" : "no"}
                  </div>
                  {p.status !== "ready" && p.impact ? <div className="probe-impact">Impact: {redactUserinfo(p.impact)}</div> : null}
                  {p.recovery ? <div className="probe-recovery">Recovery: {redactUserinfo(p.recovery)}</div> : null}
                </div>
              )}
              <div className="debug-actions">
                <button type="button" onClick={() => probe(c)} disabled={busy}>Probe</button>
                <button type="button" onClick={() => edit(c)} disabled={busy}>Edit</button>
                <button type="button" onClick={() => setEnabled(c, !c.enabled)} disabled={busy}>
                  {c.enabled ? "Disable" : "Enable"}
                </button>
                <button type="button" onClick={() => remove(c)} disabled={busy || c.has_observed_assets}
                  title={c.has_observed_assets ? "this connection has observed assets — disable it instead of removing (evidence keeps its source identity)" : undefined}>
                  Remove
                </button>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
