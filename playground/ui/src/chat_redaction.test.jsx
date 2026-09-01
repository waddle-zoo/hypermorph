import React from "react";
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { Message, GovernedBlocked, AgentControls, AssetSearch, redactDeep } from "@hyperset/chat-ui";

// hy-6tsw9 #452: free-text is redacted at the DATA/PROPS boundary (`redactDeep`), one
// shape-independent choke point per component, so a credential-bearing URL in ANY server
// field the chat renders never reaches the DOM. These DOM tests replay the adversary's
// full mutation set (the round-1 per-render + source-regex guard was BYPASSABLE): the LLM
// answer (`message.content`), stage detail, sql error, the resolution JSON dump, the error
// banner, the provider-fault message/detail/recovery, the selected agent detail, and the
// catalog error. A leaked substring in the rendered text FAILS -- the class is closed by
// construction, verified at render, not by a source pattern.

const SECRETS = ["supersecret", "tok3n99", "leaked77", "bannersecret", "answerpw", "recov3ry", "agentpw", "catalogpw"];

afterEach(() => {
  vi.restoreAllMocks();
});

describe("chat free-text redaction boundary", () => {
  it("redacts URL userinfo in the LLM answer, stage detail, sql error, and resolution JSON dump", () => {
    const message = {
      role: "assistant",
      status: "ready",
      createdAt: Date.now(),
      // The biggest surface the adversary flagged: a model can echo a credential URL in
      // its own answer. It must render only from the redacted copy.
      content: "Here is the fix: connect https://admin:answerpw@warehouse.example/db and retry.",
      stages: [
        { stage: "resolving", title: "Resolving", detail: "read https://user:supersecret@warehouse.example/db" },
      ],
      sql: { sql: "SELECT 1", error: "connect https://svc:tok3n99@db.example/x failed", rows: [], columns: [] },
      resolutionError: { code: "x", detail: "https://user:leaked77@gw.example/v1" },
      result: { trace: [] },
      bundle: null,
    };
    const { container } = render(<Message message={message} onContinue={() => {}} />);
    const dom = container.textContent;
    for (const secret of SECRETS) expect(dom).not.toContain(secret);
    expect(dom).not.toContain("user:supersecret@");
    expect(dom).not.toContain("admin:answerpw@");
    // The non-secret text survives so the answer/diagnostics stay useful.
    for (const kept of ["warehouse.example", "db.example", "gw.example", "Here is the fix"]) {
      expect(dom).toContain(kept);
    }
  });

  it("redacts URL userinfo in the error banner", () => {
    const message = {
      role: "assistant",
      status: "error",
      createdAt: Date.now(),
      error: "turn failed talking to https://u:bannersecret@h.example/x",
      stages: [],
      result: null,
    };
    const { container } = render(<Message message={message} onContinue={() => {}} />);
    expect(container.textContent).not.toContain("bannersecret");
    expect(container.textContent).toContain("h.example");
  });

  it("redacts the provider-fault message, detail, AND recovery", () => {
    // GovernedBlocked renders three free-text fields from the server error; all three
    // must come from the redacted copy, recovery included (a round-1 miss).
    const result = {
      context_source: "discovery_provider_error",
      context_resolution: {
        error: {
          message: "provider call failed for https://m:recov3ry@api.example/v1",
          detail: "base_url https://m:recov3ry@api.example/v1 rejected the request",
          recovery: "fix the credential at https://m:recov3ry@api.example/v1 and retry",
        },
      },
    };
    const { container } = render(<GovernedBlocked blocked={{}} result={result} onContinue={() => {}} />);
    const dom = container.textContent;
    expect(dom).not.toContain("recov3ry");
    expect(dom).not.toContain("m:recov3ry@");
    expect(dom).toContain("api.example");
  });

  it("redacts the selected agent detail in AgentControls", () => {
    const agents = [
      { value: "ops", label: "Ops", detail: "operator agent via https://svc:agentpw@backend.example/cfg" },
    ];
    const { container } = render(
      <AgentControls agent="ops" model="m" setAgent={() => {}} setModel={() => {}} agents={agents} models={[]} backendHealthy />,
    );
    expect(container.textContent).not.toContain("agentpw");
    expect(container.textContent).toContain("backend.example");
  });

  it("redacts URL userinfo in the catalog load error", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(
      new Error("connect https://svc:catalogpw@catalog.example/list failed"),
    );
    const { container } = render(
      <AssetSearch apiRoot="" attachments={[]} onAttach={() => {}} onClose={() => {}} />,
    );
    await waitFor(() => expect(container.textContent).toContain("catalog.example"));
    expect(container.textContent).not.toContain("catalogpw");
    expect(container.textContent).not.toContain("svc:catalogpw@");
  });
});

describe("redactDeep shape-independence (the boundary primitive)", () => {
  it("scrubs userinfo from strings at any depth -- nested objects, arrays, mixed", () => {
    const scrubbed = redactDeep({
      top: "https://a:top@h.example/x",
      nested: { deep: { s: "https://b:deep@h.example/y" } },
      list: ["plain", "https://c:inlist@h.example/z", { s: "https://d:inobj@h.example/w" }],
      num: 7,
      nil: null,
    });
    const flat = JSON.stringify(scrubbed);
    for (const secret of ["a:top@", "b:deep@", "c:inlist@", "d:inobj@"]) {
      expect(flat).not.toContain(secret);
    }
    // Non-string values and the non-secret scheme/host are preserved.
    expect(scrubbed.num).toBe(7);
    expect(scrubbed.nil).toBeNull();
    expect(scrubbed.top).toBe("https://h.example/x");
    expect(scrubbed.list[0]).toBe("plain");
    expect(flat).toContain("h.example");
  });
});
