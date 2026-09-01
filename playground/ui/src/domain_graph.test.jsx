import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { DomainGraphView } from "./main.jsx";

// hy-ha1jv: the domain graph must be legible — distinct/non-truncated source labels, a human
// navigation affordance (node selection), and a legend for node/edge kinds. Prior render
// truncated every source to the shared "table:postgres:analytics" prefix and had no
// interaction or legend.

const bundle = {
  domain_graph: {
    nodes: [
      { id: "concept:recognized_revenue", kind: "concept", label: "recognized_revenue" },
      { id: "table:postgres:analytics:finance_orders_daily", kind: "source", label: "table:postgres:analytics:finance_orders_daily" },
      { id: "table:postgres:analytics:finance_refunds_daily", kind: "source", label: "table:postgres:analytics:finance_refunds_daily" },
    ],
    edges: [
      { from: "concept:recognized_revenue", to: "table:postgres:analytics:finance_orders_daily", relation: "derived_from" },
      { from: "concept:recognized_revenue", to: "table:postgres:analytics:finance_refunds_daily", relation: "derived_from" },
    ],
  },
};

describe("DomainGraphView legibility (hy-ha1jv)", () => {
  it("renders distinct, non-truncated source labels (not the shared prefix)", () => {
    const { container } = render(<DomainGraphView bundle={bundle} />);
    const labels = [...container.querySelectorAll(".domain-graph-node-label")].map((n) => n.textContent);
    // The two sources are distinguishable — the old front-truncation collapsed both to
    // "table:postgres:analytics".
    expect(labels).toContain("finance_orders_daily");
    expect(labels).toContain("finance_refunds_daily");
    expect(new Set(labels).size).toBe(labels.length); // all distinct
    expect(labels.some((l) => l === "table:postgres:analytics")).toBe(false);
  });

  it("is interactive: clicking a node selects it (aria-pressed) and shows its degree", () => {
    render(<DomainGraphView bundle={bundle} />);
    const node = screen.getByLabelText("concept: recognized_revenue");
    expect(node.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(node);
    expect(node.getAttribute("aria-pressed")).toBe("true");
    // The detail panel reports the selected node's relationship count (unique to the detail).
    expect(screen.getByText("2 relationships")).toBeTruthy();
    // Clicking again clears the selection.
    fireEvent.click(node);
    expect(node.getAttribute("aria-pressed")).toBe("false");
  });

  it("is keyboard-operable: Enter selects a node", () => {
    render(<DomainGraphView bundle={bundle} />);
    const node = screen.getByLabelText("source: table:postgres:analytics:finance_orders_daily");
    fireEvent.keyDown(node, { key: "Enter" });
    expect(node.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText("1 relationship")).toBeTruthy();
  });

  it("renders a legend covering the node and edge kinds", () => {
    render(<DomainGraphView bundle={bundle} />);
    const legend = screen.getByLabelText("Graph legend");
    expect(within(legend).getByText("concept")).toBeTruthy();
    expect(within(legend).getByText("source")).toBeTruthy();
    expect(within(legend).getByText("derived_from")).toBeTruthy();
  });
});
