import React, { useState } from "react";
import { redactDeep } from "@hyperset/chat-ui";

// The three byte-parity ops the console drives over the EXISTING HTTP surface (hy-05nw9).
// No new op: the console POSTs the same /v0/<op> routes an MCP client would call.
export const CONSOLE_OPS = ["discover_analytics_context", "resolve_analytics_context", "validate_analytics_plan"];

const SAMPLE_QUESTION = "What is recognized revenue by customer region?";

// A recipe is an ordered list of parameterized steps. The default walks discover -> resolve ->
// validate so a first run is meaningful; every param is editable.
export function defaultRecipe() {
  return {
    name: "discover → resolve → validate",
    steps: [
      { op: "discover_analytics_context", params: { query: SAMPLE_QUESTION, limit: 20 } },
      {
        op: "resolve_analytics_context",
        params: { query: SAMPLE_QUESTION, directive: { domains: ["revenue"], concepts: [] } },
      },
      { op: "validate_analytics_plan", params: { query: SAMPLE_QUESTION, directive: { domains: ["revenue"], concepts: [] } } },
    ],
  };
}

// Build validate params from a resolved bundle (mirrors the playground's validate call), so a
// discover->resolve->validate recipe chains client-side without the caller pasting a bundle_id.
export function deriveValidateParams(bundle) {
  if (!bundle || !bundle.bundle_id) return null;
  const instructions = bundle.instructions || {};
  return {
    query: bundle.request?.query,
    directive: bundle.request?.directive,
    bundle_id: bundle.bundle_id,
    source_refs: (instructions.approved_sources || [])
      .map((item) => (typeof item === "string" ? item : item.ref))
      .filter(Boolean),
    fields: instructions.fields || [],
    joins: instructions.joins || [],
    filters: instructions.filters || [],
    grain: instructions.grain || null,
    checks: instructions.validations || [],
  };
}

const _len = (value) => (Array.isArray(value) ? value.length : 0);

// The provenance / abstention / stale / conflict / observed-only signals a reviewer scans,
// pulled from the SAME response an MCP client receives. Pure: response in, signal list out.
export function extractSignals(op, response) {
  if (!response || typeof response !== "object") return [];
  const signals = [];
  if (op === "resolve_analytics_context") {
    const resolution = response.resolution || {};
    const status = resolution.status || (response.domains ? "multi_domain" : "unknown");
    signals.push({ key: "resolution", label: "Resolution", value: status, tone: status === "governed" ? "ok" : status === "no_match" ? "bad" : "warn" });
    const authority = response.context_authority;
    signals.push({ key: "authority", label: "Authority", value: authority ? "governed" : "none — abstained", tone: authority ? "ok" : "warn" });
    signals.push({ key: "provenance", label: "Provenance refs", value: _len(response.provenance_refs), tone: _len(response.provenance_refs) ? "ok" : "info" });
    if (status === "observed_only") signals.push({ key: "observed_only", label: "Observed-only", value: "no governed authority", tone: "warn" });
    const stale = (resolution.warnings || []).filter((warning) => warning.code === "ref_awaiting_sync");
    if (stale.length) signals.push({ key: "stale", label: "Stale", value: `${stale.length} awaiting sync`, tone: "warn" });
    const conflicts = (response.linked_evidence || {}).conflicts || [];
    if (conflicts.length) {
      const worst = conflicts.some((conflict) => conflict.severity === "error") ? "error" : "warning";
      signals.push({ key: "conflict", label: "Conflicts", value: `${conflicts.length} · ${worst}`, tone: worst === "error" ? "bad" : "warn" });
    }
  } else if (op === "validate_analytics_plan") {
    const status = response.status || "unknown";
    signals.push({ key: "validation", label: "Validation", value: status, tone: status === "verified" ? "ok" : status === "unverifiable" ? "bad" : "warn" });
    if (_len(response.violations)) signals.push({ key: "violations", label: "Violations", value: _len(response.violations), tone: "bad" });
    if (_len(response.sections_not_checkable)) signals.push({ key: "gaps", label: "Not checkable", value: _len(response.sections_not_checkable), tone: "warn" });
  } else if (op === "discover_analytics_context") {
    signals.push({ key: "candidates", label: "Candidates", value: _len(response.candidates), tone: _len(response.candidates) ? "info" : "warn" });
    signals.push({ key: "assist", label: "Assist-class", value: "non-authoritative", tone: "info" });
  }
  return signals;
}

const RECIPES_KEY = "hyperset-console-recipes";

// A saved recipe is user-editable JSON (steps[].params) that could carry a pasted credential --
// a base_url with userinfo, a token in a header. REDACT the whole recipe through the SAME
// data-boundary the responses use (redactDeep strips `scheme://userinfo@` from every string)
// so a secret is NEVER written to localStorage in cleartext (hy-05nw9 r2, critic BLOCK).
export function loadRecipes() {
  try {
    // Redact on READ too, so a secret persisted by an older build is scrubbed before it can
    // re-enter the editor.
    return redactDeep(JSON.parse(localStorage.getItem(RECIPES_KEY) || "{}"));
  } catch {
    return {};
  }
}

export function persistRecipes(recipes) {
  try {
    localStorage.setItem(RECIPES_KEY, JSON.stringify(redactDeep(recipes)));
  } catch {
    /* ignore quota */
  }
}

function Signals({ signals }) {
  if (!signals.length) return null;
  return <div className="console-signals">{signals.map((signal) => <span key={signal.key} className={`console-signal ${signal.tone}`}><b>{signal.label}</b> {String(signal.value)}</span>)}</div>;
}

// The API/MCP console (hy-05nw9): parameterized discover->resolve->validate recipes and
// client-side request replay over the existing byte-parity ops. Every request is the same
// /v0/<op> POST an MCP client makes; nothing new is served.
export function ApiConsole({ request }) {
  const [recipe, setRecipe] = useState(defaultRecipe);
  const [runs, setRuns] = useState([]); // one per executed request, newest last; each replayable
  const [saved, setSaved] = useState(loadRecipes);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const runOne = async (op, params) => {
    const at = new Date().toISOString();
    try {
      const response = await request(`/v0/${op}`, params, "POST");
      return { op, params, response: redactDeep(response), signals: extractSignals(op, response), at, ok: true };
    } catch (reason) {
      return { op, params, error: reason.message, signals: [], at, ok: false };
    }
  };

  const runRecipe = async () => {
    setBusy(true);
    setError("");
    const collected = [];
    let lastResolve = null;
    for (const step of recipe.steps) {
      let params = step.params;
      // Chain client-side: an empty-bundle validate step inherits the prior resolve's bundle.
      if (step.op === "validate_analytics_plan" && !params.bundle_id && lastResolve) {
        params = deriveValidateParams(lastResolve) || params;
      }
      const result = await runOne(step.op, params);
      collected.push(result);
      if (step.op === "resolve_analytics_context" && result.ok) lastResolve = result.response;
    }
    setRuns((prev) => [...prev, ...collected]);
    setBusy(false);
  };

  const replay = async (run) => {
    setBusy(true);
    const result = await runOne(run.op, run.params);
    setRuns((prev) => [...prev, result]);
    setBusy(false);
  };

  const updateStep = (index, patch) => setRecipe((current) => ({ ...current, steps: current.steps.map((step, i) => (i === index ? { ...step, ...patch } : step)) }));
  const updateParams = (index, text) => {
    try {
      updateStep(index, { params: JSON.parse(text) });
      setError("");
    } catch {
      setError(`Step ${index + 1}: params are not valid JSON.`);
    }
  };
  const addStep = () => setRecipe((current) => ({ ...current, steps: [...current.steps, { op: "discover_analytics_context", params: { query: SAMPLE_QUESTION } }] }));
  const removeStep = (index) => setRecipe((current) => ({ ...current, steps: current.steps.filter((_, i) => i !== index) }));

  const saveRecipe = () => {
    const key = (name || recipe.name).trim();
    if (!key) return;
    // Scrub credentials BEFORE the recipe enters saved state or storage, so neither the
    // in-memory copy nor localStorage ever holds a raw secret (hy-05nw9 r2).
    const safe = redactDeep({ ...recipe, name: key });
    const next = { ...saved, [key]: safe };
    setSaved(next);
    persistRecipes(next);
    setName("");
  };
  const loadRecipe = (key) => { if (saved[key]) setRecipe(saved[key]); };

  return <div className="console-panel">
    <p className="debug-lede">Build a parameterized discover → resolve → validate recipe and run it against the exact byte-parity ops an MCP client calls. Save recipes and replay any request — all client-side; nothing new is served.</p>

    <div className="console-recipe">
      {recipe.steps.map((step, index) => <div className="console-step" key={index}>
        <div className="console-step-head">
          <select value={step.op} onChange={(event) => updateStep(index, { op: event.target.value })}>
            {CONSOLE_OPS.map((op) => <option key={op} value={op}>{op}</option>)}
          </select>
          <button className="debug-button" onClick={() => removeStep(index)} aria-label={`Remove step ${index + 1}`}>Remove</button>
        </div>
        <textarea className="console-params" rows={4} defaultValue={JSON.stringify(step.params, null, 2)} onChange={(event) => updateParams(index, event.target.value)} />
      </div>)}
      <button className="debug-button" onClick={addStep}>Add step</button>
    </div>

    <div className="debug-actions">
      <button className="debug-button primary" disabled={busy || !recipe.steps.length} onClick={runRecipe}>{busy ? "Running…" : "Run recipe"}</button>
      <input className="console-name" placeholder="Recipe name" value={name} onChange={(event) => setName(event.target.value)} />
      <button className="debug-button" onClick={saveRecipe}>Save recipe</button>
      <select className="console-load" value="" onChange={(event) => loadRecipe(event.target.value)}>
        <option value="">Load saved…</option>
        {Object.keys(saved).map((key) => <option key={key} value={key}>{key}</option>)}
      </select>
    </div>
    {error && <div className="console-error">{error}</div>}

    <div className="console-runs">
      {runs.length === 0 && <div className="empty-debug">No requests yet. Run the recipe to see responses and their provenance / abstention / stale / conflict / observed-only signals.</div>}
      {runs.map((run, index) => <div className={`console-run ${run.ok ? "" : "failed"}`} key={index}>
        <div className="console-run-head">
          <b>{run.op}</b>
          <span className="console-run-at">{run.at}</span>
          <button className="debug-button" onClick={() => replay(run)} disabled={busy}>Replay</button>
        </div>
        {run.ok ? <Signals signals={run.signals} /> : <div className="console-error">⚠ {run.error}</div>}
        <details className="console-json"><summary>Request &amp; response</summary>
          <pre className="console-pre">{JSON.stringify({ request: { op: run.op, params: run.params }, response: run.response ?? null }, null, 2)}</pre>
        </details>
      </div>)}
    </div>
  </div>;
}
