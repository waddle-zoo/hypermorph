import React, { useEffect, useState } from "react";
import { redactDeep } from "@hyperset/chat-ui";

// The five maintainer failure classes, worst-first (matches ops/diagnostics.DIAGNOSTIC_CLASSES).
const CLASS_ORDER = ["regression", "connector_outage", "missing_model", "stale_context", "invalid_input"];
const CLASS_LABELS = {
  regression: "Regression",
  connector_outage: "Connector outage",
  missing_model: "Missing model",
  stale_context: "Stale context",
  invalid_input: "Invalid input",
};

// The maintainer diagnostics VIEW (hy-bue7r): renders the server's classification of the
// standing health signals into the five named failure classes, so an operator sees WHAT KIND
// of failure this is and what to do. Read-only; the server does the classifying.
export function DiagnosticsPanel({ requestJson }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const refresh = () => {
    setLoading(true);
    requestJson("/v0/diagnostics", null, "GET")
      .then((payload) => { setData(redactDeep(payload)); setError(""); })
      .catch((reason) => setError(reason.message || "failed to load diagnostics"))
      .finally(() => setLoading(false));
  };
  useEffect(() => { refresh(); }, []);

  const rows = data?.diagnostics || [];
  const counts = data?.counts || {};
  return <section className="diagnostics-panel">
    <div className="readiness-head">
      <h2>Maintainer diagnostics</h2>
      <button type="button" className="readiness-refresh" onClick={refresh} disabled={loading}>{loading ? "Checking…" : "Re-check"}</button>
    </div>
    {error && <p className="readiness-error">{error}</p>}
    <div className="diag-counts">
      {CLASS_ORDER.map((klass) => <span key={klass} className={`diag-count ${klass}`}>{CLASS_LABELS[klass]}: {counts[klass] || 0}</span>)}
    </div>
    {!error && !loading && rows.length === 0 && <p className="empty-debug">No failures classified — every signal is healthy.</p>}
    {CLASS_ORDER.map((klass) => {
      const group = rows.filter((row) => row.class === klass);
      if (!group.length) return null;
      return <div className="diag-group" key={klass}>
        <h3 className={`diag-class ${klass}`}>{CLASS_LABELS[klass]}</h3>
        {group.map((row, index) => <div className="diag-row" key={index}>
          <div className="diag-row-head"><b>{row.subject}</b> <span className="diag-signal">{row.signal}</span></div>
          <p className="diag-detail">{row.detail}</p>
          <p className="diag-recovery">→ {row.recovery}</p>
        </div>)}
      </div>;
    })}
  </section>;
}
