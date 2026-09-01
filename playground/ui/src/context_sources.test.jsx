import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { ContextSourcesPanel } from "./context_sources.jsx";

// Sources the slice-4 list endpoint would serve: an enabled, synced 'revenue'
// source; a never-synced pointer (no serving commit); and -- for the conflict
// case -- two enabled sources claiming one domain. None carries a secret (a
// context source is a Git pointer).
const REV = {
  source_id: "src-rev",
  repository: "https://github.com/acme/context",
  ref: "main",
  path: "domains/revenue",
  enabled: true,
  domain: "revenue",
  serving_commit: "abc123",
  last_attempt_status: "synced",
  domain_conflict: false,
  conflicting_source_ids: [],
};
const EMPTY = {
  source_id: "src-empty",
  repository: "/srv/ctx",
  ref: "main",
  path: "domains/new",
  enabled: true,
  domain: null,
  serving_commit: null,
  last_attempt_status: "never_synced",
  domain_conflict: false,
  conflicting_source_ids: [],
};

function makeRequest(listResult, overrides = {}) {
  const calls = [];
  const request = vi.fn((path, payload, method = "POST") => {
    calls.push({ path, payload, method });
    if (path === "/v0/context/sources" && method === "GET") {
      return Promise.resolve({ sources: listResult });
    }
    if (path in overrides) return overrides[path](payload);
    return Promise.resolve({});
  });
  return { request, calls };
}

async function renderPanel(listResult = [REV, EMPTY], overrides) {
  const { request, calls } = makeRequest(listResult, overrides);
  render(<ContextSourcesPanel requestJson={request} />);
  await waitFor(() =>
    expect(document.querySelectorAll("article.sources-row").length).toBe(listResult.length),
  );
  return { request, calls };
}

describe("ContextSourcesPanel", () => {
  it("lists sources with pointer, domain, serving commit, and sync status", async () => {
    await renderPanel();
    const row = screen.getByText(/acme\/context@main:domains\/revenue/).closest("article");
    expect(within(row).getByText("src-rev")).toBeInTheDocument();
    expect(within(row).getByText("revenue")).toBeInTheDocument();
    expect(within(row).getByText("abc123")).toBeInTheDocument();
    expect(within(row).getByText("synced")).toBeInTheDocument();
  });

  it("Sync, Validate, Disable, and Remove hit the right endpoints", async () => {
    const { calls } = await renderPanel([REV], {
      "/v0/context/sources/validate": () => Promise.resolve({ result: { status: "valid", ok: true, reasons: [] } }),
    });
    const row = screen.getByText(/acme\/context/).closest("article");
    fireEvent.click(within(row).getByRole("button", { name: "Sync" }));
    await waitFor(() => expect(calls).toContainEqual({ path: "/v0/context/sources/sync", payload: { source_id: "src-rev" }, method: "POST" }));
    fireEvent.click(within(row).getByRole("button", { name: "Validate" }));
    const status = await within(row).findByText(/valid/);
    expect(status).toBeInTheDocument();
    expect(calls).toContainEqual({ path: "/v0/context/sources/validate", payload: { source_id: "src-rev" }, method: "POST" });
    fireEvent.click(within(row).getByRole("button", { name: "Disable" }));
    await waitFor(() => expect(calls).toContainEqual({ path: "/v0/context/sources/enable", payload: { source_id: "src-rev", enabled: false }, method: "POST" }));
  });

  it("Validate re-fetches the sources so the recorded card reflects the live result", async () => {
    // hy-ppufd: Validate now records the live attempt, so the panel reloads the list and
    // the card's recorded status flips from a stale 'synced' to the live 'failed'.
    let synced = { ...REV };
    const request = vi.fn((path, payload, method = "POST") => {
      if (path === "/v0/context/sources" && method === "GET") {
        return Promise.resolve({ sources: [synced] });
      }
      if (path === "/v0/context/sources/validate") {
        // The server recorded 'failed'; the next list GET must show it.
        synced = { ...REV, last_attempt_status: "failed", last_error: "git fetch failed: Could not read from remote repository." };
        return Promise.resolve({ result: { status: "invalid", ok: false, reasons: ["git fetch failed: Could not read from remote repository."] } });
      }
      return Promise.resolve({});
    });
    render(<ContextSourcesPanel requestJson={request} />);
    await waitFor(() => expect(document.querySelectorAll("article.sources-row").length).toBe(1));
    const row = screen.getByText(/acme\/context/).closest("article");
    expect(within(row).getByText("synced")).toBeInTheDocument();
    fireEvent.click(within(row).getByRole("button", { name: "Validate" }));
    // The recorded card is reloaded and now reads 'failed' -- recorded == live.
    await within(row).findByText("failed");
    const getCalls = request.mock.calls.filter(
      ([path, , method]) => path === "/v0/context/sources" && method === "GET",
    );
    expect(getCalls.length).toBeGreaterThanOrEqual(2); // initial load + the post-validate refresh
  });

  it("Add posts a source, and a never-synced pointer is removable", async () => {
    const { calls } = await renderPanel([EMPTY], {
      "/v0/context/sources": (payload) => payload
        ? Promise.resolve({ source: { source_id: "src-new" } })
        : Promise.resolve({ sources: [EMPTY] }),
    });
    fireEvent.change(screen.getByLabelText("repository"), { target: { value: "/srv/x" } });
    fireEvent.change(screen.getByLabelText("path"), { target: { value: "domains/x" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(calls).toContainEqual({ path: "/v0/context/sources", payload: { repository: "/srv/x", ref: "main", path: "domains/x" }, method: "POST" }));
    // The never-synced pointer's Remove is enabled and hits /remove.
    const row = screen.getByText(/\/srv\/ctx@main/).closest("article");
    const removeBtn = within(row).getByRole("button", { name: "Remove" });
    expect(removeBtn).not.toBeDisabled();
    fireEvent.click(removeBtn);
    await waitFor(() => expect(calls).toContainEqual({ path: "/v0/context/sources/remove", payload: { source_id: "src-empty" }, method: "POST" }));
  });

  it("a source with governed history is disable-only: Remove is disabled", async () => {
    await renderPanel([REV]);
    const row = screen.getByText(/acme\/context/).closest("article");
    expect(within(row).getByRole("button", { name: "Remove" })).toBeDisabled();
  });

  it("surfaces a domain conflict prominently and names the conflicting sources", async () => {
    const a = { ...REV, source_id: "src-a", domain_conflict: true, conflicting_source_ids: ["src-b"] };
    const b = { ...REV, source_id: "src-b", repository: "/srv/b", domain_conflict: true, conflicting_source_ids: ["src-a"] };
    await renderPanel([a, b]);
    expect(screen.getAllByText(/domain conflict — not served/).length).toBe(2);
    const rowA = screen.getByText(/acme\/context@main/).closest("article");
    expect(within(rowA).getByText("src-b")).toBeInTheDocument();
  });

  it("Enable is disabled when enabling would re-create a collision", async () => {
    // A disabled source whose domain is already served by an enabled source.
    const disabled = { ...REV, source_id: "src-dis", repository: "/srv/dis", enabled: false, serving_commit: "old" };
    await renderPanel([REV, disabled]);
    const row = screen.getByText(/\/srv\/dis@main/).closest("article");
    expect(within(row).getByRole("button", { name: "Enable" })).toBeDisabled();
  });

});
