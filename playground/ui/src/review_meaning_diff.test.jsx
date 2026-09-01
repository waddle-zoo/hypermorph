import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ReviewMeaningDiff, ReviewTaskItem } from "./main.jsx";

const NOOP = { onPropose: () => {}, onEdit: () => {}, onUndo: () => {}, onAssignSelf: () => {}, onUnassign: () => {}, busy: false, canPropose: false };

describe("ReviewMeaningDiff (hy-z6zv: the exact current-vs-proposed diff at detail)", () => {
  it("renders added, removed, and changed entries per section, plus a grain move", () => {
    const diff = {
      sections: {
        definitions: { added: [{ term: "expansion" }], removed: [{ term: "legacy" }], changed: [{ identity: "churn" }] },
        fields: { added: [{ name: "churn_rate" }], removed: [], changed: [] },
      },
      grain: { before: null, after: "monthly" },
    };
    render(<ReviewMeaningDiff diff={diff} />);
    expect(screen.getByText("+ expansion")).toBeTruthy();
    expect(screen.getByText("− legacy")).toBeTruthy();
    expect(screen.getByText("~ churn")).toBeTruthy();
    expect(screen.getByText("+ churn_rate")).toBeTruthy();
    expect(screen.getByText("~ null → monthly")).toBeTruthy();
  });

  it("says so when nothing changed rather than rendering an empty diff", () => {
    render(<ReviewMeaningDiff diff={{ sections: {} }} />);
    expect(screen.getByText(/No change/)).toBeTruthy();
  });
});

describe("ReviewTaskItem shows current meaning beside the draft and the uncertainty", () => {
  const draft = { definitions: [{ term: "churn", statement: "customers lost in a period" }] };

  it("renders the governed current meaning beside the proposed draft when one exists", () => {
    const task = {
      id: "t1",
      proposal_payload: { domain: "revenue", definition: draft, miss: { question: "churn?" } },
      current_meaning: { definitions: [{ term: "churn", statement: "old governed meaning" }] },
      proposed_diff: { sections: { definitions: { added: [], removed: [], changed: [{ identity: "churn" }] } } },
      uncertainty: { undeclared_concepts: ["expansion_revenue"], assist: true },
    };
    render(<ReviewTaskItem task={task} {...NOOP} />);
    expect(screen.getByText("Current governed meaning")).toBeTruthy();
    expect(screen.getByText("old governed meaning")).toBeTruthy();   // current, beside...
    expect(screen.getByText("customers lost in a period")).toBeTruthy(); // ...the proposed draft
    expect(screen.getByText("~ churn")).toBeTruthy();                 // the exact diff at detail
    // The unresolved uncertainty is sourced from the served `uncertainty` field.
    expect(screen.getByText(/Undeclared: expansion_revenue/)).toBeTruthy();
  });

  it("states that nothing is governed yet when there is no current meaning", () => {
    const task = {
      id: "t2",
      proposal_payload: { domain: "revenue", definition: draft },
      current_meaning: null,
      proposed_diff: { sections: { definitions: { added: [{ term: "churn" }], removed: [], changed: [] } } },
      uncertainty: { undeclared_concepts: [], assist: true },
    };
    render(<ReviewTaskItem task={task} {...NOOP} />);
    expect(screen.getByText(/Nothing governed for this domain yet/)).toBeTruthy();
    expect(screen.getByText("+ churn")).toBeTruthy();
  });
});
