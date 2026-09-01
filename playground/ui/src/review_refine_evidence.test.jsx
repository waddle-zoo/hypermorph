import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ReviewTaskItem } from "./main.jsx";

const NOOP = { onPropose: () => {}, onEdit: () => {}, onPreview: () => {}, onUndo: () => {}, onAssignSelf: () => {}, onUnassign: () => {}, busy: false, canPropose: false };
const TASK = {
  id: "t1",
  proposal_payload: { domain: "revenue", definition: { definitions: [{ term: "churn", statement: "customers lost" }] }, miss: { question: "churn?" } },
  current_meaning: null,
  proposed_diff: { sections: {} },
  uncertainty: { undeclared_concepts: [], assist: true },
};

describe("ReviewTaskItem wires refine (hy-to8m part 1)", () => {
  it("opens a feedback box and calls onRefine with the task id and typed feedback", () => {
    const onRefine = vi.fn();
    render(<ReviewTaskItem task={TASK} onRefine={onRefine} onRequestEvidence={() => {}} {...NOOP} />);
    // The feedback box is hidden until the reviewer asks to refine.
    expect(screen.queryByPlaceholderText(/be more precise/)).toBeNull();
    fireEvent.click(screen.getByText("Refine with agent"));
    const box = screen.getByPlaceholderText(/be more precise/);
    fireEvent.change(box, { target: { value: "be more precise about the period" } });
    fireEvent.click(screen.getByText("Send to agent"));
    expect(onRefine).toHaveBeenCalledWith("t1", "be more precise about the period");
    // The box closes after sending.
    expect(screen.queryByPlaceholderText(/be more precise/)).toBeNull();
  });
});

describe("ReviewTaskItem wires request-evidence (hy-to8m part 2)", () => {
  it("calls onRequestEvidence with the task id", () => {
    const onRequestEvidence = vi.fn();
    render(<ReviewTaskItem task={TASK} onRefine={() => {}} onRequestEvidence={onRequestEvidence} {...NOOP} />);
    fireEvent.click(screen.getByText("Re-gather evidence"));
    expect(onRequestEvidence).toHaveBeenCalledWith("t1");
  });
});
