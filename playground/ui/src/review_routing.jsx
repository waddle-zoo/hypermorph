import React from "react";

// Reviewer routing on a PROPOSED review card (hq-hig7): who reviews, and WHERE
// the proposal goes, so a reviewer sees both before acting. It reads the
// `review_routing` slice-8 recorded on the task's proposal_payload at propose
// time (never the propose response -- surfacing it there would change a served
// shape). Two honest states: ROUTED shows the reviewer handle(s) and the target
// identity (repository + base_ref); NEEDS-ROUTING is a distinct, actionable
// state -- the target has no reviewer routing, so the card says so and links an
// admin to configure it, rather than implying a reviewer that does not exist.

// Defense-in-depth on the render boundary: redact URL userinfo from the target
// repository, the SAME canonical `scheme://userinfo@` rule the server uses
// (#431) and connections.jsx applies. The server already redacts free text (that
// is the boundary); this only guarantees the UI cannot render a credential a
// field somehow carried. Pure string replace -- never throws.
const URL_USERINFO_G = /([a-zA-Z][a-zA-Z0-9+.\-]*:\/\/)[^/]*@/g;

export function redactUserinfo(value) {
  return typeof value === "string" ? value.replace(URL_USERINFO_G, "$1") : value;
}

export function ReviewRouting({ routing }) {
  if (!routing) return null;
  const target = routing.target || {};
  const repository = redactUserinfo(target.repository);
  const baseRef = target.base_ref;
  const reviewers = routing.reviewers || [];

  if (routing.status === "needs_routing" || reviewers.length === 0) {
    // Distinct, actionable: NOT a reviewer, and NOT a silent drop. Configure
    // routing on the write-back target this proposal went to.
    return <div className="review-routing needs-routing" role="status">
      <span className="review-label">Reviewer routing</span>
      <p className="review-routing-needs">
        ⚠ No reviewer is routed for this target
        {repository ? <> (<b>{repository}</b>)</> : null}. The proposal is open, but
        nobody is assigned to review it.
      </p>
      <a className="linklike" href="/admin/">Configure reviewer routing on the write-back target ↗</a>
    </div>;
  }

  return <div className="review-routing routed">
    <span className="review-label">Reviewer routing</span>
    <div className="review-routing-reviewers">
      Reviewer{reviewers.length > 1 ? "s" : ""}: {reviewers.map((r, i) =>
        <span className="review-reviewer" key={r || i}>{r}</span>
      ).reduce((acc, el, i) => (i === 0 ? [el] : [...acc, ", ", el]), [])}
    </div>
    {(repository || baseRef) && <div className="review-routing-target">
      Proposes to {repository ? <b>{repository}</b> : "your context repo"}
      {baseRef ? <> · <code>{baseRef}</code></> : null}
    </div>}
  </div>;
}
