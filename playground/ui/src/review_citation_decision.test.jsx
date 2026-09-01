import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { ReviewTaskItem, ReviewPage } from "./main.jsx";

// hy-n8ms3: the /review surface records a human include/exclude/approve/reject on a cited
// source. Acting writes a durable citation_decisions row via /v0/review/citations/decide; the
// card mirrors the returned decision. These prove the browser path exists and calls the route.

const ME = "auth0|me@https://issuer.example";
const CITATION = "superset:dataset:finance_orders_daily";

function decisionTask(id = "rt-1") {
  return {
    id,
    proposal_payload: {
      domain: "revenue",
      definition: { definitions: [{ term: "recognized revenue", statement: "net of tax" }] },
      gathered_sources: [{ ref: CITATION }],
    },
  };
}

const NOOP = {
  onPropose: () => {},
  onEdit: () => {},
  onPreview: () => {},
  onRefine: () => {},
  onRequestEvidence: () => {},
  onAssignSelf: () => {},
  onUnassign: () => {},
  onUndo: () => {},
  busy: false,
  canPropose: false,
};

describe("ReviewTaskItem citation decision controls", () => {
  it("calls onDecide with the task id, citation ref, source ref, and choice", () => {
    const onDecide = vi.fn();
    render(<ReviewTaskItem task={decisionTask()} onDecide={onDecide} {...NOOP} />);
    fireEvent.click(screen.getByRole("button", { name: `exclude cited source ${CITATION}` }));
    expect(onDecide).toHaveBeenCalledWith("rt-1", CITATION, CITATION, "exclude");
  });

  it("groups the compact choices and names every button with its cited source", () => {
    render(<ReviewTaskItem task={decisionTask()} onDecide={() => {}} {...NOOP} />);
    const group = screen.getByRole("group", { name: `Decision for cited source ${CITATION}` });
    expect(group).toHaveClass("citation-decisions");
    expect(within(group).getAllByRole("button")).toHaveLength(4);
    for (const button of within(group).getAllByRole("button")) {
      expect(button).toHaveAccessibleName(new RegExp(CITATION));
    }
  });

  it("shows the recorded decision returned from the server", () => {
    const decisions = { [CITATION]: { decision: "approve", decided_by: ME } };
    render(<ReviewTaskItem task={decisionTask()} onDecide={() => {}} decisions={decisions} {...NOOP} />);
    expect(screen.getByText("Decided: approve")).toBeTruthy();
    expect(screen.getByRole("button", { name: `approve cited source ${CITATION}` })).toHaveAttribute("aria-pressed", "true");
  });

  it("renders no decision controls when onDecide is not provided (existing surfaces unchanged)", () => {
    render(<ReviewTaskItem task={decisionTask()} {...NOOP} />);
    expect(screen.queryByRole("button", { name: `include cited source ${CITATION}` })).toBeNull();
  });

  it("surfaces the affected asset as decidable even when the payload has no gathered/approved sources", () => {
    // The real drift finding (approved_expression_drift) carries its cited source ONLY in
    // affected_asset_ids, with empty gathered/approved sources. The control must still appear
    // so a human can decide (hy-n8ms3, found live).
    const onDecide = vi.fn();
    const driftTask = { id: "rt-2", affected_asset_ids: ["oa-a3f19b9c9f31"], proposal_payload: { domain: "revenue" } };
    render(<ReviewTaskItem task={driftTask} onDecide={onDecide} {...NOOP} />);
    fireEvent.click(screen.getByRole("button", { name: "approve cited source oa-a3f19b9c9f31" }));
    expect(onDecide).toHaveBeenCalledWith("rt-2", "oa-a3f19b9c9f31", "oa-a3f19b9c9f31", "approve");
  });

  it("renders one evidence row when gathered and approved sources name the same ref", () => {
    const task = {
      ...decisionTask(),
      proposal_payload: {
        ...decisionTask().proposal_payload,
        gathered_sources: [{ ref: CITATION, signals: ["observed"] }],
        definition: {
          definitions: [{ term: "recognized revenue", statement: "net of tax" }],
          approved_sources: [{ ref: CITATION, role: "primary" }],
        },
      },
    };
    render(<ReviewTaskItem task={task} onDecide={() => {}} {...NOOP} />);
    expect(screen.getByText("Evidence · 1 observed source")).toBeTruthy();
    expect(screen.getAllByTitle(CITATION)).toHaveLength(2); // evidence + cited-source controls
  });

  it("renders terminal tasks as read-only history", () => {
    render(<ReviewTaskItem task={{ ...decisionTask("rt-terminal"), status: "resolved" }} {...NOOP} onDecide={() => {}} />);
    expect(screen.getByText("Read-only history · resolved")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Edit definition/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /Propose to Git/i })).toBeNull();
    expect(screen.queryByRole("group", { name: /Decision for cited source/ })).toBeNull();
  });
});

function makeRequest(recorder) {
  return vi.fn((path, payload) => {
    if (path === "/v0/list_review_tasks") return Promise.resolve({ tasks: [decisionTask()] });
    if (path === "/v0/review/writeback-config") return Promise.resolve({ config: null });
    if (path === "/v0/review/whoami") return Promise.resolve({ identity: ME });
    if (path === "/v0/review/citations/decide") {
      recorder.push(payload);
      return Promise.resolve({
        decision: { decision: payload.decision, citation_ref: payload.citation_ref, decided_by: ME },
      });
    }
    return Promise.resolve({});
  });
}

describe("ReviewPage records a citation decision through the served route", () => {
  it("POSTs /v0/review/citations/decide and mirrors the decision onto the card", async () => {
    const recorder = [];
    render(<ReviewPage request={makeRequest(recorder)} />);
    await waitFor(() => expect(screen.getByRole("button", { name: `include cited source ${CITATION}` })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: `include cited source ${CITATION}` }));
    await waitFor(() => expect(screen.getByText("Decided: include")).toBeTruthy());
    expect(recorder).toEqual([
      { review_task_id: "rt-1", citation_ref: CITATION, source_ref: CITATION, decision: "include" },
    ]);
  });
});
