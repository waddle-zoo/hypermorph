import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { DiagnosticsPanel } from "./diagnostics.jsx";

const PAYLOAD = {
  counts: { regression: 1, connector_outage: 1, missing_model: 0, stale_context: 1, invalid_input: 0 },
  diagnostics: [
    { class: "regression", subject: "ref_not_observed", signal: "resolution.warnings", detail: "a governed ref is gone", recovery: "reconcile" },
    { class: "connector_outage", subject: "Prod", signal: "observed_status", detail: "unreachable", recovery: "check url" },
    { class: "stale_context", subject: "git_context", signal: "admin_readiness", detail: "stale", recovery: "sync" },
  ],
};

describe("DiagnosticsPanel renders the classified maintainer failures", () => {
  it("shows counts and groups rows by class", async () => {
    const requestJson = vi.fn(() => Promise.resolve(PAYLOAD));
    render(<DiagnosticsPanel requestJson={requestJson} />);
    await waitFor(() => expect(screen.getByText("ref_not_observed")).toBeTruthy());
    expect(requestJson).toHaveBeenCalledWith("/v0/diagnostics", null, "GET");
    // Grouped headings for the classes that have rows.
    expect(screen.getByText("Regression")).toBeTruthy();
    expect(screen.getByText("Connector outage")).toBeTruthy();
    expect(screen.getByText("Stale context")).toBeTruthy();
    // Counts render.
    expect(screen.getByText(/Regression: 1/)).toBeTruthy();
    expect(screen.getByText(/Missing model: 0/)).toBeTruthy();
    // A row shows its subject, detail, and recovery.
    expect(screen.getByText("a governed ref is gone")).toBeTruthy();
    expect(screen.getByText("→ reconcile")).toBeTruthy();
  });

  it("says everything is healthy when there are no diagnostics", async () => {
    const requestJson = vi.fn(() => Promise.resolve({ counts: {}, diagnostics: [] }));
    render(<DiagnosticsPanel requestJson={requestJson} />);
    await waitFor(() => expect(screen.getByText(/every signal is healthy/)).toBeTruthy());
  });
});
