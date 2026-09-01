import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ReviewPreviewPanel, ReviewTaskItem } from "./main.jsx";

const NOOP = { onPropose: () => {}, onEdit: () => {}, onUndo: () => {}, onAssignSelf: () => {}, onUnassign: () => {}, busy: false, canPropose: false };

const PREVIEW = {
  not_serving: true,
  task_id: "t1",
  representative_questions: ["What is churn?", "What does 'churn' mean?"],
  regression_checks: [
    { check: "proposed_definition_validates", status: "pass", detail: [] },
    { check: "preserves_existing_governed_meaning", status: "warn", detail: ["definitions: changed churn"] },
  ],
};

describe("ReviewPreviewPanel (hy-nauw: ephemeral preview render)", () => {
  it("renders representative questions and regression checks with their status", () => {
    render(<ReviewPreviewPanel preview={PREVIEW} />);
    expect(screen.getByText("Preview · not serving")).toBeTruthy();
    expect(screen.getByText("What is churn?")).toBeTruthy();
    expect(screen.getByText("proposed_definition_validates")).toBeTruthy();
    // the warn check surfaces its regression detail
    expect(screen.getByText("preserves_existing_governed_meaning")).toBeTruthy();
    expect(screen.getByText("definitions: changed churn")).toBeTruthy();
    const statuses = screen.getAllByText(/pass|warn/).map((n) => n.textContent);
    expect(statuses).toContain("pass");
    expect(statuses).toContain("warn");
  });
});

describe("ReviewTaskItem Preview button fetches and renders the ephemeral preview", () => {
  const task = {
    id: "t1",
    proposal_payload: { domain: "revenue", definition: { definitions: [{ term: "churn", statement: "customers lost" }] }, miss: { question: "churn?" } },
    current_meaning: null,
    proposed_diff: { sections: {} },
    uncertainty: { undeclared_concepts: [], assist: true },
  };

  it("calls onPreview with the task id and renders the returned panel", async () => {
    const onPreview = vi.fn().mockResolvedValue(PREVIEW);
    render(<ReviewTaskItem task={task} onPreview={onPreview} {...NOOP} />);
    // The panel is not shown until the reviewer asks for it.
    expect(screen.queryByText("Preview · not serving")).toBeNull();
    fireEvent.click(screen.getByText("Preview"));
    expect(onPreview).toHaveBeenCalledWith("t1");
    await waitFor(() => expect(screen.getByText("Preview · not serving")).toBeTruthy());
    expect(screen.getByText("What is churn?")).toBeTruthy();
  });
});
