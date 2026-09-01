import React, { useCallback, useEffect, useState } from "react";

// Admin Write-back Targets (hq-1aap): manage the per-domain proposal routing
// targets served by the slice-2 admin endpoints (list/add/update/enable/delete/
// test). It lists each target's pointer and routing/state fields plus a SECRET
// REFERENCE only (an env var NAME, an App id, or an "encrypted · set" flag --
// never the value, which the backend never sends to the browser). It offers
// add/edit, enable/disable, delete (the default/catch-all target is
// disable-only, never deletable here so routing's fallback is not silently
// removed), and a read-only reachability probe ("Test target") that opens NO
// branch or PR. Configuring a target approves, merges, and proposes nothing
// (ADR 0012).

const EMPTY_DRAFT = {
  routing_key: "",
  repository: "",
  base_ref: "main",
  manifest_path: "",
  reviewer_routing: "",
  token_source: "env_ref",
  token_ref: "",
  token: "",
  app_id: "",
  app_private_key: "",
};

// The secret REFERENCE for a target, never a value: an env var NAME (env_ref),
// the GitHub App id (github_app), or just whether an encrypted secret is set.
// The token ciphertext / App private key are never returned by the backend, so
// there is nothing here that could render a secret.
function secretReference(t) {
  if (t.token_source === "github_app") {
    return t.app_id ? `GitHub App #${t.app_id}${t.token_set ? " · key set" : ""}` : "GitHub App (no key)";
  }
  if (t.token_source === "encrypted") {
    return t.token_set ? "encrypted · set" : "encrypted · none";
  }
  return t.token_ref ? `env: ${t.token_ref}` : "none";
}

export function WritebackTargetsPanel({ requestJson }) {
  const [targets, setTargets] = useState(null);
  const [draft, setDraft] = useState(EMPTY_DRAFT);
  const [editingId, setEditingId] = useState(null);
  const [probeByTarget, setProbeByTarget] = useState({});
  // The default/catch-all target is disable-only (no keyed edit/delete), but its
  // reviewer routing must be editable -- a proposal that falls back to the
  // default is the common case, and without this its needs-routing action was
  // dead (hq-hig7, #435 round 3). This is a reviewer-ONLY editor on the default
  // row: null = closed, a string = the in-progress value.
  const [defaultReviewerDraft, setDefaultReviewerDraft] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(() => {
    requestJson("/v0/review/writeback-targets", null, "GET")
      .then((data) => { setTargets(data.targets || []); setError(""); })
      .catch((reason) => setError(reason.message));
  }, [requestJson]);
  useEffect(() => { refresh(); }, [refresh]);

  const save = (event) => {
    event.preventDefault();
    if (!draft.routing_key.trim() || !draft.repository.trim() || !draft.manifest_path.trim()) {
      setError("routing key, repository, and manifest path are required");
      return;
    }
    setBusy(true); setError("");
    // The pasted token and App private key are WRITE-ONLY: sent only when
    // (re-)entered, never read back and never pre-filled on edit.
    const payload = {
      routing_key: draft.routing_key.trim(),
      repository: draft.repository.trim(),
      base_ref: draft.base_ref.trim() || "main",
      manifest_path: draft.manifest_path.trim(),
      // The reviewer handle(s) a proposal routed here goes to (hq-hig7);
      // comma-separated, OPTIONAL. Sent every save so clearing it removes the
      // routing (the honest needs-routing state the review card links here to fix).
      reviewer_routing: draft.reviewer_routing.trim(),
      token_source: draft.token_source,
    };
    if (draft.token_source === "env_ref") payload.token_ref = draft.token_ref;
    else if (draft.token_source === "encrypted") { if (draft.token) payload.token = draft.token; }
    else { payload.app_id = draft.app_id; if (draft.app_private_key) payload.app_private_key = draft.app_private_key; }
    requestJson("/v0/review/writeback-targets", payload, "POST")
      .then(() => { setDraft(EMPTY_DRAFT); setEditingId(null); refresh(); })
      .catch((reason) => setError(reason.message))
      .finally(() => setBusy(false));
  };

  const edit = (t) => {
    setEditingId(t.id);
    setDraft({
      routing_key: t.routing_key || "",
      repository: t.repository,
      base_ref: t.base_ref,
      manifest_path: t.manifest_path,
      reviewer_routing: t.reviewer_routing || "",
      token_source: t.token_source || "env_ref",
      token_ref: t.token_ref || "",
      token: "",
      app_id: t.app_id == null ? "" : String(t.app_id),
      app_private_key: "",
    });
  };
  const cancelEdit = () => { setEditingId(null); setDraft(EMPTY_DRAFT); };

  const setEnabled = (t, enabled) => {
    setBusy(true); setError("");
    requestJson("/v0/review/writeback-targets/enable", { id: t.id, enabled }, "POST")
      .then(refresh).catch((reason) => setError(reason.message)).finally(() => setBusy(false));
  };
  const remove = (t) => {
    setBusy(true); setError("");
    requestJson("/v0/review/writeback-targets/delete", { id: t.id }, "POST")
      .then(refresh).catch((reason) => setError(reason.message)).finally(() => setBusy(false));
  };
  const test = (t) => {
    setBusy(true); setError("");
    requestJson("/v0/review/writeback-targets/test", { id: t.id }, "POST")
      .then((data) => setProbeByTarget((prev) => ({ ...prev, [t.id]: data.probe })))
      .catch((reason) => setError(reason.message)).finally(() => setBusy(false));
  };
  // Update ONLY the reviewer routing on the DEFAULT target, via the write-back
  // CONFIG endpoint (the default is not a keyed target). The default's existing
  // config and secret REFERENCES are carried through so this reviewer-only edit
  // never clobbers them (a blank secret box keeps the stored ciphertext); no
  // secret value is sent, and routing_key stays null so the default keeps its
  // identity. It never deletes or disables the default.
  const saveDefaultReviewers = (t) => {
    setBusy(true); setError("");
    const tokenSource = t.token_source || "env_ref";
    const payload = {
      repository: t.repository,
      base_ref: t.base_ref,
      manifest_path: t.manifest_path,
      token_source: tokenSource,
      reviewer_routing: (defaultReviewerDraft || "").trim(),
    };
    if (tokenSource === "env_ref") payload.token_ref = t.token_ref || "";
    else if (tokenSource === "github_app") payload.app_id = t.app_id == null ? "" : String(t.app_id);
    requestJson("/v0/review/writeback-config", payload, "POST")
      .then(() => { setDefaultReviewerDraft(null); refresh(); })
      .catch((reason) => setError(reason.message)).finally(() => setBusy(false));
  };

  const probeClass = (status) => (status === "ready" ? "ready" : status === "degraded" ? "unknown" : "blocked");

  return (
    <section className="writeback-targets-panel">
      <h2>Write-back targets</h2>
      <p className="sources-note">
        Per-domain proposal routing. A review proposal is routed to the target whose routing key
        equals its domain, else the default (catch-all) target; a disabled or unreachable target is
        excluded (fail closed). Secrets are stored server-side and shown as a reference only.
        Configuring a target approves and merges nothing (ADR 0012).
      </p>
      {error && <p className="readiness-error" role="alert">{error}</p>}
      <form className="sources-add" onSubmit={save}>
        <input aria-label="routing key" placeholder="routing key (domain)" value={draft.routing_key}
          disabled={editingId !== null}
          onChange={(e) => setDraft({ ...draft, routing_key: e.target.value })} />
        <input aria-label="repository" placeholder="repository (url or local path)" value={draft.repository}
          onChange={(e) => setDraft({ ...draft, repository: e.target.value })} />
        <input aria-label="base ref" placeholder="base ref" value={draft.base_ref}
          onChange={(e) => setDraft({ ...draft, base_ref: e.target.value })} />
        <input aria-label="manifest path" placeholder="manifest path" value={draft.manifest_path}
          onChange={(e) => setDraft({ ...draft, manifest_path: e.target.value })} />
        <input aria-label="reviewer routing" placeholder="reviewer(s), comma-separated (optional)"
          value={draft.reviewer_routing}
          onChange={(e) => setDraft({ ...draft, reviewer_routing: e.target.value })} />
        <select aria-label="token source" value={draft.token_source}
          onChange={(e) => setDraft({ ...draft, token_source: e.target.value })}>
          <option value="github_app">GitHub App</option>
          <option value="env_ref">Env var name</option>
          <option value="encrypted">Paste token (encrypted)</option>
        </select>
        {draft.token_source === "env_ref"
          ? <input aria-label="token reference" placeholder="server-side env var NAME" value={draft.token_ref}
              onChange={(e) => setDraft({ ...draft, token_ref: e.target.value })} />
          : draft.token_source === "encrypted"
          ? <input aria-label="token" type="password" autoComplete="off" placeholder="paste token (write-only)"
              value={draft.token} onChange={(e) => setDraft({ ...draft, token: e.target.value })} />
          : <>
              <input aria-label="app id" placeholder="App ID" value={draft.app_id}
                onChange={(e) => setDraft({ ...draft, app_id: e.target.value })} />
              <input aria-label="app private key" type="password" autoComplete="off"
                placeholder="App private key .pem (write-only)" value={draft.app_private_key}
                onChange={(e) => setDraft({ ...draft, app_private_key: e.target.value })} />
            </>}
        <button type="submit" disabled={busy}>{editingId !== null ? "Update target" : "Add target"}</button>
        {editingId !== null && <button type="button" onClick={cancelEdit} disabled={busy}>Cancel</button>}
      </form>
      <div className="sources-grid">
        {targets === null && <p className="empty-debug">Loading…</p>}
        {targets !== null && targets.length === 0 && <p className="empty-debug">No write-back targets configured.</p>}
        {(targets || []).map((t) => {
          const probe = probeByTarget[t.id];
          return (
            <article className="sources-row" key={t.id}>
              <div className="sources-row-head">
                <b>{t.is_default ? "(default catch-all)" : t.routing_key} → {t.repository}@{t.base_ref}</b>
                <span className={`status-badge ${t.enabled ? "ready" : "unknown"}`}>{t.enabled ? "enabled" : "disabled"}</span>
              </div>
              <dl className="readiness-meta">
                <div><dt>Routing key</dt><dd>{t.is_default ? "default (catch-all)" : t.routing_key}</dd></div>
                <div><dt>Manifest</dt><dd>{t.manifest_path}</dd></div>
                <div><dt>Reviewers</dt><dd>{t.reviewer_routing || "none (needs routing)"}</dd></div>
                <div><dt>Secret</dt><dd>{secretReference(t)}</dd></div>
                <div><dt>Last test</dt><dd>{t.test_result || "never"}</dd></div>
              </dl>
              {probe && (
                <p className={`status-badge probe-result ${probeClass(probe.status)}`} role="status">
                  {probe.status}{probe.reason ? ` — ${probe.reason}` : ""}
                </p>
              )}
              <div className="debug-actions">
                <button type="button" onClick={() => test(t)} disabled={busy}>Test target</button>
                {!t.is_default && <button type="button" onClick={() => edit(t)} disabled={busy}>Edit</button>}
                {/* The default is disable-only, but its reviewer routing is still
                    editable so a default-routed proposal can be routed. */}
                {t.is_default && defaultReviewerDraft === null &&
                  <button type="button" onClick={() => setDefaultReviewerDraft(t.reviewer_routing || "")} disabled={busy}>Set reviewers</button>}
                <button type="button" onClick={() => setEnabled(t, !t.enabled)} disabled={busy}>
                  {t.enabled ? "Disable" : "Enable"}
                </button>
                {!t.is_default && <button type="button" onClick={() => remove(t)} disabled={busy}>Delete</button>}
              </div>
              {t.is_default && defaultReviewerDraft !== null &&
                <div className="default-reviewer-edit">
                  <input aria-label="default reviewer routing" placeholder="reviewer(s), comma-separated (optional)"
                    value={defaultReviewerDraft} onChange={(e) => setDefaultReviewerDraft(e.target.value)} />
                  <button type="button" onClick={() => saveDefaultReviewers(t)} disabled={busy}>Save reviewers</button>
                  <button type="button" onClick={() => setDefaultReviewerDraft(null)} disabled={busy}>Cancel</button>
                </div>}
            </article>
          );
        })}
      </div>
    </section>
  );
}
