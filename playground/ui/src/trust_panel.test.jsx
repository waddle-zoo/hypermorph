import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { TrustPanel, TRUST_STATES } from "@hyperset/chat-ui";

// hy-icx1 (Explorer 5+7): the per-answer trust/provenance disclosure is FIRST-CLASS
// (labels + next actions), not collapsed JSON, and observed_only / no_match / stale-
// conflict / timeout each get a distinct labeled state with a next action.

function governedMessage(over = {}) {
  return {
    role: "assistant",
    status: "ready",
    result: {
      bundle_id: "cb-123",
      provider: "openai",
      model: "gpt-5.6-luna",
      agent_label: "Revenue analyst",
      agent_config: { policy_result: "allowed" },
      context_resolution: { status: "resolved" },
    },
    bundle: {
      bundle_id: "cb-123",
      resolution: { status: "governed", warnings: [] },
      context_authority: { path: "domains/revenue", commit_sha: "abc1234" },
      linked_evidence: { conflicts: [] },
    },
    ...over,
  };
}

describe("TrustPanel", () => {
  it("renders a governed answer's trust state and provenance as first-class fields", () => {
    render(<TrustPanel message={governedMessage()} />);
    expect(screen.getByText("Governed")).toBeInTheDocument();
    expect(screen.getByText("cb-123")).toBeInTheDocument();
    expect(screen.getByText(/domains\/revenue @ abc1234/)).toBeInTheDocument();
    expect(screen.getByText("Revenue analyst")).toBeInTheDocument();
    expect(screen.getByText(/openai · gpt-5.6-luna/)).toBeInTheDocument();
    expect(screen.getByText("allowed")).toBeInTheDocument();
  });

  it("labels observed_only as not-governed-trusted with a next action", () => {
    const m = governedMessage({
      bundle: { resolution: { status: "observed_only", warnings: [] }, context_authority: {}, linked_evidence: {} },
    });
    render(<TrustPanel message={m} />);
    expect(screen.getByText("Observed only")).toBeInTheDocument();
    expect(screen.getByText(/Next:/)).toBeInTheDocument();
    expect(screen.getByText(/Explore context|refine/i)).toBeInTheDocument();
  });

  it("labels no_match/abstention with a refine next action", () => {
    const m = governedMessage({
      bundle: { resolution: { status: "no_match", warnings: [] }, context_authority: {}, linked_evidence: {} },
    });
    render(<TrustPanel message={m} />);
    expect(screen.getByText("No governed match")).toBeInTheDocument();
    expect(screen.getByText(/Refine the question/i)).toBeInTheDocument();
  });

  it("calls out a stale/conflict warning with a reconcile next action", () => {
    const m = governedMessage({
      bundle: {
        resolution: {
          status: "mixed",
          warnings: [{ code: "stale_bundle", message: "the served pin is stale" }],
        },
        context_authority: { commit_sha: "abc1234" },
        linked_evidence: { conflicts: [] },
      },
    });
    render(<TrustPanel message={m} />);
    expect(screen.getByText("stale_bundle")).toBeInTheDocument();
    expect(screen.getByText(/re-sync the source or reconcile/i)).toBeInTheDocument();
  });

  it("gives a timeout its own labeled state and retry next action", () => {
    render(
      <TrustPanel message={{ role: "assistant", status: "error", error: "agent harness exceeded its 300-second execution timeout" }} />,
    );
    expect(screen.getByText("Timed out")).toBeInTheDocument();
    expect(screen.getByText(/Retry the question/i)).toBeInTheDocument();
  });

  it("renders nothing when there is no governed status and no timeout", () => {
    const { container } = render(
      <TrustPanel message={{ role: "assistant", status: "ready", result: { context_source: "governed_blocked" } }} />,
    );
    expect(container.querySelector(".trust-panel")).toBeNull();
  });


  it("never renders URL userinfo in a warning message (defense-in-depth, #448)", () => {
    const m = governedMessage({
      bundle: {
        resolution: {
          status: "mixed",
          warnings: [
            {
              code: "ref_not_observed",
              message:
                "evidence ref 'superset:dataset:https://user:supersecret@gateway.example/v1' is not observed",
            },
          ],
        },
        context_authority: { commit_sha: "abc1234" },
        linked_evidence: { conflicts: [] },
      },
    });
    const { container } = render(<TrustPanel message={m} />);
    // The credential is gone from the rendered DOM...
    expect(container.textContent).not.toContain("supersecret");
    expect(container.textContent).not.toContain("user:supersecret@");
    // ...while the code and the non-secret host remain.
    expect(screen.getByText("ref_not_observed")).toBeInTheDocument();
    expect(container.textContent).toContain("gateway.example");
  });

  it("exposes the four served resolution statuses", () => {
    expect(Object.keys(TRUST_STATES).sort()).toEqual(
      ["governed", "mixed", "no_match", "observed_only"],
    );
  });
});
