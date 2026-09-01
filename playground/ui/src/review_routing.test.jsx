import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ReviewRouting, redactUserinfo } from "./review_routing.jsx";

const ROUTED = {
  status: "routed",
  reviewers: ["alice", "bob"],
  target: { repository: "https://github.com/acme/context", base_ref: "main" },
  authority_commit: "deadbeefcafe",
  backlink: "https://github.com/acme/context/pull/7",
};

describe("ReviewRouting", () => {
  it("shows the routed reviewers AND the target identity (who reviews, and where)", () => {
    render(<ReviewRouting routing={ROUTED} />);
    // WHO reviews.
    expect(screen.getByText("alice")).toBeInTheDocument();
    expect(screen.getByText("bob")).toBeInTheDocument();
    // WHERE it proposes to: repository + base ref.
    expect(screen.getByText("https://github.com/acme/context")).toBeInTheDocument();
    expect(screen.getByText("main")).toBeInTheDocument();
    // Not the needs-routing state.
    expect(document.body.textContent).not.toContain("No reviewer is routed");
  });

  it("renders NEEDS-ROUTING as a distinct, actionable state, not a phantom reviewer", () => {
    const routing = {
      status: "needs_routing",
      reviewers: [],
      target: { repository: "https://github.com/acme/context", base_ref: "main" },
    };
    render(<ReviewRouting routing={routing} />);
    // Distinct honest state...
    expect(screen.getByText(/No reviewer is routed for this target/)).toBeInTheDocument();
    // ...and actionable: a link to configure routing on the write-back target.
    const link = screen.getByRole("link", { name: /Configure reviewer routing/ });
    expect(link).toHaveAttribute("href", "/admin/");
    // It never invents a reviewer.
    expect(document.body.textContent).not.toMatch(/Reviewer:/);
  });

  it("renders nothing when a task carries no routing (e.g. not yet proposed)", () => {
    const { container } = render(<ReviewRouting routing={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  // A credential in the target repository must not reach the DOM -- the server
  // redacts free text (the boundary), and this guarantees the UI cannot render a
  // credential a field somehow carried. Both the routed and needs-routing paths
  // render the repository, so both are covered.
  it("redacts any credential in the target repository (routed path)", () => {
    render(<ReviewRouting routing={{ ...ROUTED, target: { repository: "https://u:ghp_LEAK@github.com/acme/context", base_ref: "main" } }} />);
    expect(document.body.textContent).not.toContain("ghp_LEAK");
    expect(document.body.textContent).toContain("github.com/acme/context");
  });

  it("redacts any credential in the target repository (needs-routing path)", () => {
    render(<ReviewRouting routing={{ status: "needs_routing", reviewers: [], target: { repository: "https://u:ghp_NEEDLEAK@github.com/acme/context" } }} />);
    expect(document.body.textContent).not.toContain("ghp_NEEDLEAK");
    expect(document.body.textContent).toContain("github.com/acme/context");
  });

  it("redactUserinfo strips scheme://userinfo@ and preserves scp / port@rev", () => {
    expect(redactUserinfo("https://u:tok@host/r")).toBe("https://host/r");
    expect(redactUserinfo("git@host:o/r")).toBe("git@host:o/r"); // scp, no scheme
    expect(redactUserinfo("https://git.corp:8443/team/repo HEAD@{1}")).toContain("HEAD@{1}");
  });
});
