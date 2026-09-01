import React, { useCallback, useEffect, useState } from "react";

// Admin Context Sources (hq-kbcy): manage the Git context (read) SOURCES served
// by the slice-4 admin endpoints (list/add/sync/validate/enable-disable/remove).
// It lists each source's pointer (repository/ref/path), serving commit, snapshot
// sync status, validation state, and owner domain -- and NO secret value (a
// context source is a Git POINTER; there is nothing secret to render). It
// surfaces the estate DOMAIN-CONFLICT the resolver refuses on prominently and
// never presents an ambiguous domain as serving. Actions: add, sync, a
// read-only "Validate" dry-run, enable/disable (enable is blocked when it would
// re-create a collision), and remove (disabled for a source with governed
// history -- disable instead; only a never-synced pointer is removable, ADR
// 0012). It snapshots what Git owns; it approves and edits nothing.

const EMPTY_FORM = { repository: "", ref: "main", path: "" };


export function ContextSourcesPanel({ requestJson }) {
  const [sources, setSources] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [validationBySource, setValidationBySource] = useState({});
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(() => {
    requestJson("/v0/context/sources", null, "GET")
      .then((data) => { setSources(data.sources || []); setError(""); })
      .catch((reason) => setError(reason.message));
  }, [requestJson]);
  useEffect(() => { refresh(); }, [refresh]);

  const add = (event) => {
    event.preventDefault();
    if (!form.repository.trim() || !form.path.trim()) {
      setError("repository and path are required");
      return;
    }
    setBusy(true); setError(""); setNotice("");
    requestJson(
      "/v0/context/sources",
      { repository: form.repository.trim(), ref: form.ref.trim() || "main", path: form.path.trim() },
      "POST",
    )
      .then((data) => { setNotice(`Added ${data.source.source_id} — sync it to fetch the first snapshot`); setForm(EMPTY_FORM); refresh(); })
      .catch((reason) => setError(reason.message))
      .finally(() => setBusy(false));
  };

  const sync = (s) => {
    setBusy(true); setError(""); setNotice("");
    requestJson("/v0/context/sources/sync", { source_id: s.source_id }, "POST")
      .then((data) => {
        const r = data.result;
        const reasons = (r.reasons || []).join("; ");
        setNotice(r.ok ? `Synced ${s.source_id}: ${r.status} @ ${r.commit || "?"}` : `Sync failed: ${reasons || r.status}`);
        refresh();
      })
      .catch((reason) => setError(reason.message))
      .finally(() => setBusy(false));
  };

  const validate = (s) => {
    setBusy(true); setError("");
    requestJson("/v0/context/sources/validate", { source_id: s.source_id }, "POST")
      .then((data) => {
        setValidationBySource((prev) => ({ ...prev, [s.source_id]: data.result }));
        // Validate now records the live attempt (hy-ppufd), so reload the card's
        // recorded status/last error -- recorded == live, not a stale green.
        refresh();
      })
      .catch((reason) => setError(reason.message))
      .finally(() => setBusy(false));
  };

  const setEnabled = (s, enabled) => {
    setBusy(true); setError(""); setNotice("");
    requestJson("/v0/context/sources/enable", { source_id: s.source_id, enabled }, "POST")
      .then(refresh).catch((reason) => setError(reason.message)).finally(() => setBusy(false));
  };

  const remove = (s) => {
    setBusy(true); setError(""); setNotice("");
    requestJson("/v0/context/sources/remove", { source_id: s.source_id }, "POST")
      .then(() => { setNotice(`Removed ${s.source_id}`); refresh(); })
      .catch((reason) => setError(reason.message)).finally(() => setBusy(false));
  };

  // The domains an ENABLED source already serves, so a DISABLED source whose
  // domain is among them cannot be enabled without re-creating the collision the
  // backend refuses -- the button is pre-disabled (the server CONFIGURE gate +
  // collision check remain the real boundary).
  const servedDomains = new Set(
    (sources || []).filter((s) => s.enabled && s.domain && s.serving_commit).map((s) => s.domain),
  );

  return (
    <section className="context-sources-panel">
      <h2>Context sources</h2>
      <p className="sources-note">
        Manage the Git context authority pointers (repo · ref · path). This snapshots what Git
        owns; it never approves or edits governed meaning (ADR 0012). A source is a pointer, so
        there is no secret here. A domain claimed by more than one enabled source is AMBIGUOUS and
        is not served -- disable all but one to reconcile.
      </p>
      {error && <p className="readiness-error" role="alert">{error}</p>}
      {notice && <p className="sources-notice" role="status">{notice}</p>}
      <form className="sources-add" onSubmit={add}>
        <input aria-label="repository" placeholder="repository (url or local path)" value={form.repository}
          onChange={(e) => setForm({ ...form, repository: e.target.value })} />
        <input aria-label="ref" placeholder="ref" value={form.ref}
          onChange={(e) => setForm({ ...form, ref: e.target.value })} />
        <input aria-label="path" placeholder="path" value={form.path}
          onChange={(e) => setForm({ ...form, path: e.target.value })} />
        <button type="submit" disabled={busy}>Add</button>
      </form>
      <div className="sources-grid">
        {sources === null && <p className="empty-debug">Loading…</p>}
        {sources !== null && sources.length === 0 && <p className="empty-debug">No context sources configured.</p>}
        {(sources || []).map((s) => {
          const validation = validationBySource[s.source_id];
          const hasHistory = Boolean(s.serving_commit);
          const wouldCollide = !s.enabled && s.domain && servedDomains.has(s.domain);
          return (
            <article className="sources-row" key={s.source_id}>
              <div className="sources-row-head">
                <b>{s.repository}@{s.ref}:{s.path}</b>
                <span className={`status-badge ${s.enabled ? "ready" : "unknown"}`}>{s.enabled ? "enabled" : "disabled"}</span>
                {s.domain_conflict && <span className="status-badge blocked" role="status">domain conflict — not served</span>}
              </div>
              <dl className="readiness-meta">
                <div><dt>Source ID</dt><dd>{s.source_id}</dd></div>
                <div><dt>Owner domain</dt><dd>{s.domain || "—"}</dd></div>
                <div><dt>Serving commit</dt><dd>{s.serving_commit || "never pinned"}</dd></div>
                <div><dt>Sync status</dt><dd>{s.last_attempt_status || "never"}</dd></div>
                {s.last_error && <div><dt>Last error</dt><dd>{s.last_error}</dd></div>}
                {s.domain_conflict && (
                  <div><dt>Conflicts with</dt><dd>{(s.conflicting_source_ids || []).join(", ")}</dd></div>
                )}
              </dl>
              {validation && (
                <p className={`status-badge validation-result ${validation.ok ? "ready" : "blocked"}`} role="status">
                  {validation.status}{validation.reasons && validation.reasons.length ? ` — ${validation.reasons.join("; ")}` : ""}
                </p>
              )}
              <div className="debug-actions">
                <button type="button" onClick={() => sync(s)} disabled={busy}>Sync</button>
                <button type="button" onClick={() => validate(s)} disabled={busy}>Validate</button>
                {s.enabled
                  ? <button type="button" onClick={() => setEnabled(s, false)} disabled={busy}>Disable</button>
                  : <button type="button" onClick={() => setEnabled(s, true)} disabled={busy || wouldCollide}
                      title={wouldCollide ? "another enabled source already serves this domain — disable it first" : undefined}>Enable</button>}
                <button type="button" onClick={() => remove(s)} disabled={busy || hasHistory}
                  title={hasHistory ? "this source has governed history — disable it instead of removing (ADR 0012)" : undefined}>Remove</button>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
