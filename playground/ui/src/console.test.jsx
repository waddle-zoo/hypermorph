import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ApiConsole, extractSignals, deriveValidateParams, persistRecipes, loadRecipes } from "./console.jsx";

// This vitest env has no localStorage, so install a minimal in-memory stub. Recipes save and
// load through it, letting the save-path redaction (hy-05nw9 r2) be tested end to end.
beforeEach(() => {
  const store = new Map();
  globalThis.localStorage = {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: (key) => store.delete(key),
    clear: () => store.clear(),
  };
});

const RECIPES_KEY = "hyperset-console-recipes";
const SECRET_URL = "https://user:supersecret@embed.internal/v1";

describe("extractSignals surfaces provenance / abstention / stale / conflict / observed-only", () => {
  it("a governed resolve shows authority, provenance, and no warnings", () => {
    const signals = extractSignals("resolve_analytics_context", {
      resolution: { status: "governed", warnings: [] },
      context_authority: { commit: "abc" },
      provenance_refs: ["git_context:x@1", "observed_version:y"],
      linked_evidence: { conflicts: [] },
    });
    const by = Object.fromEntries(signals.map((s) => [s.key, s]));
    expect(by.resolution.value).toBe("governed");
    expect(by.resolution.tone).toBe("ok");
    expect(by.authority.value).toBe("governed");
    expect(by.provenance.value).toBe(2);
    expect(by.observed_only).toBeUndefined();
    expect(by.stale).toBeUndefined();
    expect(by.conflict).toBeUndefined();
  });

  it("an observed-only, stale, conflicting resolve surfaces every warning signal (abstained authority)", () => {
    const signals = extractSignals("resolve_analytics_context", {
      resolution: { status: "observed_only", warnings: [{ code: "ref_awaiting_sync" }] },
      context_authority: null,
      provenance_refs: [],
      linked_evidence: { conflicts: [{ severity: "error" }, { severity: "warning" }] },
    });
    const by = Object.fromEntries(signals.map((s) => [s.key, s]));
    expect(by.authority.value).toBe("none — abstained");
    expect(by.authority.tone).toBe("warn");
    expect(by.observed_only).toBeTruthy();
    expect(by.stale.value).toBe("1 awaiting sync");
    expect(by.conflict.value).toBe("2 · error");
    expect(by.conflict.tone).toBe("bad");
  });

  it("validate surfaces status and violations", () => {
    const ok = extractSignals("validate_analytics_plan", { status: "verified", violations: [] });
    expect(ok[0]).toMatchObject({ key: "validation", value: "verified", tone: "ok" });
    const bad = extractSignals("validate_analytics_plan", { status: "unverifiable", violations: [{ code: "no_governed_context" }] });
    const by = Object.fromEntries(bad.map((s) => [s.key, s]));
    expect(by.validation.tone).toBe("bad");
    expect(by.violations.value).toBe(1);
  });
});

describe("deriveValidateParams chains a resolve bundle into a validate request", () => {
  it("pulls bundle_id, directive, and approved source refs from the bundle", () => {
    const params = deriveValidateParams({
      bundle_id: "cb_1",
      request: { query: "q", directive: { domains: ["revenue"] } },
      instructions: { approved_sources: [{ ref: "table:x" }, "table:y"], fields: [{ name: "f" }] },
    });
    expect(params.bundle_id).toBe("cb_1");
    expect(params.source_refs).toEqual(["table:x", "table:y"]);
    expect(params.fields).toEqual([{ name: "f" }]);
  });
  it("returns null without a bundle_id", () => {
    expect(deriveValidateParams({})).toBeNull();
  });
});

function makeRequest(overrides = {}) {
  const calls = [];
  const request = vi.fn((path, params, method = "POST") => {
    calls.push({ path, params, method });
    if (path in overrides) return Promise.resolve(overrides[path]);
    return Promise.resolve({});
  });
  return { request, calls };
}

describe("ApiConsole runs a recipe and replays a request client-side", () => {
  const overrides = {
    "/v0/discover_analytics_context": { candidates: [{ ref: "a" }] },
    "/v0/resolve_analytics_context": {
      bundle_id: "cb_1",
      request: { query: "q", directive: { domains: ["revenue"] } },
      resolution: { status: "governed", warnings: [] },
      context_authority: { commit: "abc" },
      provenance_refs: ["git_context:x@1"],
      instructions: { approved_sources: ["table:x"] },
      linked_evidence: { conflicts: [] },
    },
    "/v0/validate_analytics_plan": { status: "verified", violations: [] },
  };

  it("runs the three ops over the /v0 byte-parity routes and shows their signals", async () => {
    const { request, calls } = makeRequest(overrides);
    render(<ApiConsole request={request} />);
    fireEvent.click(screen.getByText("Run recipe"));
    await waitFor(() => expect(screen.getAllByText(/Provenance refs/).length).toBeGreaterThan(0));
    // It called the exact byte-parity op routes an MCP client uses -- no new op.
    expect(calls.map((c) => c.path)).toEqual([
      "/v0/discover_analytics_context",
      "/v0/resolve_analytics_context",
      "/v0/validate_analytics_plan",
    ]);
    // The validate step CHAINED the resolve's bundle_id client-side.
    expect(calls[2].params.bundle_id).toBe("cb_1");
    expect(screen.getAllByText("governed").length).toBeGreaterThan(0); // resolution + authority signals
    expect(screen.getByText("verified")).toBeTruthy(); // validation signal
  });

  it("replays a prior request by re-issuing the same call", async () => {
    const { request, calls } = makeRequest(overrides);
    render(<ApiConsole request={request} />);
    fireEvent.click(screen.getByText("Run recipe"));
    await waitFor(() => expect(calls.length).toBe(3));
    fireEvent.click(screen.getAllByText("Replay")[0]);
    await waitFor(() => expect(calls.length).toBe(4));
    expect(calls[3].path).toBe("/v0/discover_analytics_context"); // the first run replayed
  });
});

describe("saved recipes never persist a raw secret (hy-05nw9 r2)", () => {
  it("persistRecipes strips URL userinfo before writing to localStorage", () => {
    persistRecipes({
      r: { name: "r", steps: [{ op: "resolve_analytics_context", params: { base_url: SECRET_URL } }] },
    });
    const raw = globalThis.localStorage.getItem(RECIPES_KEY);
    expect(raw).not.toContain("supersecret"); // nothing raw on disk
    expect(JSON.parse(raw).r.steps[0].params.base_url).toBe("https://embed.internal/v1");
  });

  it("loadRecipes redacts a secret a legacy build persisted in cleartext", () => {
    globalThis.localStorage.setItem(
      RECIPES_KEY,
      JSON.stringify({ r: { steps: [{ op: "x", params: { u: SECRET_URL } }] } }),
    );
    const loaded = loadRecipes();
    expect(loaded.r.steps[0].params.u).toBe("https://embed.internal/v1");
    expect(JSON.stringify(loaded)).not.toContain("supersecret");
  });

  it("saving a recipe from the UI persists it redacted, never in cleartext", async () => {
    const request = vi.fn(() => Promise.resolve({}));
    render(<ApiConsole request={request} />);
    // Put a credential-bearing param into the first step, then save.
    const params = document.querySelectorAll("textarea.console-params")[0];
    fireEvent.change(params, {
      target: { value: JSON.stringify({ query: "q", base_url: SECRET_URL }) },
    });
    fireEvent.change(screen.getByPlaceholderText("Recipe name"), { target: { value: "creds" } });
    fireEvent.click(screen.getByText("Save recipe"));
    const raw = globalThis.localStorage.getItem(RECIPES_KEY);
    expect(raw).toBeTruthy();
    expect(raw).not.toContain("supersecret"); // the save path redacted it
    expect(raw).toContain("https://embed.internal/v1");
  });
});
