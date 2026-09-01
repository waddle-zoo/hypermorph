import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { GovernedBlocked } from "@hyperset/chat-ui";

// hy-yts5j: a context-discovery PROVIDER fault must not be rendered as the
// corpus-blaming "No governed metadata found" -- that swallowed the real cause
// (bad base URL / model / credential) and pointed the operator at their governed
// context instead of their config. The backend attributes it as
// context_source === "discovery_provider_error" and carries the real provider
// error in context_resolution.error.detail; the UI must surface it.
const PROVIDER_FAULT = {
  context_source: "discovery_provider_error",
  context_resolution: {
    status: "unresolved",
    error: {
      code: "context_discovery_provider_error",
      message: "The openai model provider failed the context-discovery call.",
      recovery: "Check the model provider configuration (credentials, base URL, and model name).",
      detail: "Error code: 404 - model 'gpt-5.6-luna' not found",
    },
  },
};

describe("GovernedBlocked", () => {
  it("surfaces a provider/config fault and its real detail, not the corpus message", () => {
    render(<GovernedBlocked blocked={{ question: "q" }} result={PROVIDER_FAULT} onContinue={() => {}} />);
    expect(screen.getByText(/failed at the model provider/i)).toBeInTheDocument();
    // The REAL provider error is shown so the operator can fix config...
    expect(screen.getByText(/model 'gpt-5.6-luna' not found/)).toBeInTheDocument();
    expect(screen.getByText(/base URL/i)).toBeInTheDocument();
    // ...and it is NOT mislabeled as an empty catalog.
    expect(screen.queryByText(/No governed metadata found/i)).toBeNull();
  });

  it("still shows the corpus message for a genuine empty-catalog block", () => {
    render(
      <GovernedBlocked
        blocked={{ question: "q" }}
        result={{ context_source: "governed_blocked", context_resolution: { status: "unresolved" } }}
        onContinue={() => {}}
      />,
    );
    expect(screen.getByText(/No governed metadata found/i)).toBeInTheDocument();
    expect(screen.queryByText(/failed at the model provider/i)).toBeNull();
  });

  it("offers Continue without governed context on a provider fault", () => {
    const onContinue = vi.fn();
    render(<GovernedBlocked blocked={{ question: "q" }} result={PROVIDER_FAULT} onContinue={onContinue} />);
    screen.getByRole("button", { name: /Continue without governed context/i }).click();
    expect(onContinue).toHaveBeenCalledWith({ question: "q" });
  });
});
